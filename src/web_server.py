import os
import json
import base64
import struct
import logging
import asyncio
import sys
from aiohttp import web, WSMsgType
from src.settings import settings

logger = logging.getLogger("WEB")
ws_logger = logging.getLogger("WS")
rest_logger = logging.getLogger("REST")

# Global state
valves = [0] * 8
reported_valves = [0] * 8
time_stamp = 0
remote_stamp = 0
ws_connected = False
online = False
connection_state = 2
battery_percent = "?"
sm = 0
iv = None

bin_fields = {
    'DAY': 6,
    'TIME_LOW': 8,
    'TIME_HIGH': 9,
    'BUTTONS': 12,
    'BATTERY': 13,
    'STATE': 14
}

states = {
    0: 'online',
    1: 'just offline',
    2: 'offline for more than 5 minutes'
}

# WebSocket Clients
clients = set()
channels = {} # Map channel (MAC) to websocket connection

# --- Helper Functions ---

def update_states(bin_state):
    global remote_stamp, time_stamp, battery_percent, connection_state, reported_valves

    remote_stamp = bin_state[bin_fields['TIME_LOW']] + (bin_state[bin_fields['TIME_HIGH']] * 256)
    time_stamp = remote_stamp
    battery = bin_state[bin_fields['BATTERY']]
    battery_percent = battery * 1.4428 - 268
    connection_state = bin_state[bin_fields['STATE']]
    buttons = bin_state[bin_fields['BUTTONS']]

    logger.info(f"Battery is roughly at {int(battery_percent)}%")

    reported_valves[0] = buttons & 0x1
    reported_valves[1] = buttons & 0x2
    reported_valves[2] = buttons & 0x4
    reported_valves[3] = buttons & 0x8
    reported_valves[4] = buttons & 0x11
    reported_valves[5] = buttons & 0x22
    reported_valves[6] = buttons & 0x44
    reported_valves[7] = buttons & 0x88

    logger.info(f"BUTTONS: {reported_valves}")

async def send_message(event, data, channel_id=None):
    if channel_id is None:
        channel_id = settings.mac.lower()

    payload = json.dumps({
        'event': event,
        'data': str(data),
        'channel': channel_id
    })

    if channel_id in channels:
        ws_client = channels[channel_id]
        if not ws_client.closed:
            ws_logger.debug(f"Sending message: {event} to channel {channel_id}")
            await ws_client.send_str(payload)
    else:
        for ws_client in clients:
            if not ws_client.closed:
                ws_logger.debug(f"Sending message: {event} to broadcast")
                await ws_client.send_str(payload)

async def send_raw_message(msg):
    channel_id = settings.mac.lower()
    if channel_id in channels:
        ws_client = channels[channel_id]
        if not ws_client.closed:
             ws_logger.debug(f"Sending RAW message to channel {channel_id}")
             await ws_client.send_str(msg)
    else:
        for ws_client in clients:
            if not ws_client.closed:
                ws_logger.debug(f"Sending RAW message to broadcast")
                await ws_client.send_str(msg)

async def send_long_message(event, data, channel_id=None):
    if channel_id is None:
        channel_id = settings.mac.lower()

    buffer = bytearray(134)
    try:
        valve_id = int(settings.valveId, 16)
    except ValueError:
        logger.error(f"Invalid valveId in settings: {settings.valveId}. Using 0.")
        valve_id = 0

    # Write valveId (2 bytes LE)
    struct.pack_into('<H', buffer, 0, valve_id)
    # Write data (2 bytes LE) at offset 4
    struct.pack_into('<H', buffer, 4, data)

    b64_data = base64.b64encode(buffer).decode('utf-8')

    payload = json.dumps({
        'event': event,
        'data': b64_data,
        'channel': channel_id
    })

    if channel_id in channels:
        ws_client = channels[channel_id]
        if not ws_client.closed:
             await ws_client.send_str(payload)
    else:
        for ws_client in clients:
            if not ws_client.closed:
                await ws_client.send_str(payload)


async def msg_manual_sched(channel_arg=None, runtime=None):
    logger.info(f"Updating valve state {valves}")
    dbg = ''

    buffer = bytearray(18)
    try:
        valve_id = int(settings.valveId, 16)
    except ValueError:
        logger.error(f"Invalid valveId in settings: {settings.valveId}. Using 0.")
        valve_id = 0

    struct.pack_into('<H', buffer, 0, valve_id)

    for i in range(len(valves)):
        t = int(valves[i])
        if t > time_stamp:
            dbg += f"V{i}:{t - time_stamp} "
            struct.pack_into('<H', buffer, 2 + 2 * i, t)
        else:
            dbg += f"V{i}:OFF "
            valves[i] = 0

    ws_logger.debug(f"VALVES : {dbg}")

    # The original code wraps the base64 string in quotes
    b64_data = f'"{base64.b64encode(buffer).decode("utf-8")}"'

    ev = {
        'event': 'manual_sched',
        'data': b64_data,
        'channel': settings.mac.lower()
    }

    ws_logger.debug(f"Constructed msg : {json.dumps(ev)}")
    await send_raw_message(json.dumps(ev))
    return True

async def msg_sched_day(day):
    await send_long_message(f"sched_day{day}", 0)

async def msg_timestamp(time, extra=0):
    b = bytearray(3)
    struct.pack_into('<H', b, 0, int(time))
    struct.pack_into('b', b, 2, 0)

    await send_message('timestamp', base64.b64encode(b).decode('utf-8'))

async def msg_hashkey(key):
    await send_message('hash_key', f'"{key}"')

async def msg_rev_req():
    await send_message('rev_request', '')

async def msg_connection_established():
    await send_message('pusher:connection_established', '{"socket_id":"265216.826472"}')

async def check_timeout():
    global time_stamp
    time_stamp += 1
    logger.debug(f"Watchdog : time:{time_stamp}/{remote_stamp}")

    dbg = ''
    for i in range(len(valves)):
        t = int(valves[i])
        if t > time_stamp:
             dbg += f"V{i}:{t - time_stamp} "
        else:
             dbg += f"V{i}:OFF "
             valves[i] = 0
    logger.debug(f"VALVES : {dbg}")
    # sendPing(wss.clients) - using aiohttp heartbeat instead

# --- Handlers ---

async def index(request):
    return web.FileResponse('./web/index.html')

async def handle_rest(request):
    global valves, online
    opts = request.query
    rest_logger.debug(f"Rest API call with opts {opts}")

    if 'channel' not in opts:
        # Status request
        dbg = {}
        for i in range(len(valves)):
            dbg[f"V{i}"] = str(valves[i])
        for i in range(len(reported_valves)):
            dbg[f"R{i}"] = str(reported_valves[i])
        dbg["systime"] = str(time_stamp)

        status_data = {
            "status": "OK",
            "online": states.get(connection_state, "unknown"),
            "battery": str(battery_percent),
            "valves": dbg
        }
        return web.json_response(status_data)

    valve = int(opts.get('channel', 0))
    minutes = int(opts.get('min', 0))

    if minutes > 0 and valve > 0:
        rest_logger.info(f"SET CH {valve} to {minutes} minutes.")
        valves[valve-1] = minutes + time_stamp
        ws_logger.info(f"Turning ON channel {valve} for runtime {minutes}")

        if online:
            await msg_manual_sched(valve, valves[valve-1])
            return web.json_response({"status": "OK", "msg": "value updated"})

    elif valve > 0:
        rest_logger.info(f"SET CH {valve} to OFF.")
        ws_logger.info(f"Sending an OFF message for valve {valve}")
        valves[valve-1] = 0
        if online:
             await msg_manual_sched(valve, valves[valve-1])

    return web.json_response({"status": "OK", "msg": "value set to 0."})

async def handle_submit(request):
    global online, sm, iv, time_stamp, remote_stamp

    # Extract idhash from query string manually because aiohttp might interpret it differently?
    # Original: /submit/?idhash=xxxx&message=<base64>
    id_hash = request.query.get('idhash', '').replace("'", "")
    message = request.query.get('message', '')

    bin_state = None

    if message.endswith('ack--null'):
        ack_type = message.replace('ascii--', '').replace('--ack--null', '')
        logger.info(f"Device sent event ack for {ack_type} device time : {remote_stamp}.")
        bin_state = bytearray(18)
    else:
        # Some padding might be needed for base64 decoding if not valid
        # Python's base64 module is strict about padding
        padded_message = message + '=' * (-len(message) % 4)
        try:
            bin_state = base64.b64decode(padded_message)
        except Exception as e:
            logger.error(f"Error decoding base64 message: {e}")
            return web.Response(text='OK')

    # Parse remoteId (MAC address)
    if len(bin_state) >= 6:
        remote_id = ''.join(f'{b:02x}' for b in reversed(bin_state[0:6]))
    else:
        remote_id = "000000000000"

    # First message from device check (id_hash is checked against '0000000000' etc)
    if id_hash == '0000000000' or id_hash == 'ffffffffff':
        logger.info(f"Received submit for channel {remote_id}")
        if len(bin_state) >= 10:
             remote_stamp = bin_state[8] + (bin_state[9] * 256)
             time_stamp = remote_stamp

        await msg_hashkey('53f574cb08')
        return web.Response(text='OK')

    if not ws_connected:
        ws_logger.error('Device not in sync. Please reset or wait.')
        return web.Response(text='OK')

    # State Machine
    if sm < 7:
        await msg_sched_day(sm)
        sm += 1
        return web.Response(text='OK')

    if sm == 7:
        await msg_manual_sched(2, 20)
        sm += 1
        return web.Response(text='OK')

    if sm == 8:
        await msg_timestamp(time_stamp, 0x03)
        sm += 1
        return web.Response(text='OK')

    if sm == 9:
        await msg_rev_req()
        sm += 1
        return web.Response(text='OK')

    if sm == 10:
        if iv:
            iv.cancel()
        # Start watchdog loop
        iv = asyncio.create_task(watchdog_loop())
        sm += 1

    if message.startswith('ascii--revisions--E400'):
        logger.debug('Device sent revisions-E400.')
        return web.Response(text='OK')

    if remote_id == 'ffffffffffff' or remote_id == settings.mac.lower():
        update_states(bin_state)
        if connection_state == 0:
            online = True
            logger.info(f"Device online ({remote_id})")
        else:
            logger.info(f"Device not online ({remote_id})")
    elif remote_id == '000000000000':
        pass
    else:
        online = True
        logger.info(f"Device in unknown state {remote_id}")

    return web.Response(text='OK')

async def app_handler(request):
    """
    Handles requests to /app/{key}.
    If it's a WebSocket upgrade request, it initiates the WebSocket connection.
    Otherwise, it returns a standard OK response.
    """
    if request.headers.get('Upgrade', '').lower() == 'websocket':
        return await websocket_handler(request)
    else:
        rest_logger.debug(f"New Pusher client connected: {request.query}")
        return web.Response(text='OK')

async def websocket_handler(request):
    global ws_connected, online, sm
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    clients.add(ws)
    ws_connected = True
    port = request.transport.get_extra_info('peername')[1]
    ws_logger.debug(f"New WS connection established from port id {port}")

    await msg_connection_established()

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    ws_logger.debug(f"New WS event {data.get('event')} for port id {port}")

                    if data.get('event') == 'pusher:ping':
                        ws_logger.info('Received pusher ping on client')
                        await send_message('pusher:pong', '{}', settings.mac.lower())

                    elif data.get('event') == 'pusher:subscribe':
                        channel_name = data.get('data', {}).get('channel')
                        ws_logger.info(f"Received subscribe request for channel {channel_name}")
                        channels[channel_name] = ws

                        await send_message('pusher_internal:subscription_succeeded', '{}', settings.mac.lower())
                        online = False
                        sm = 0

                    else:
                        ws_logger.error('I dont know how to handle this event.')

                except Exception as e:
                    ws_logger.error(f"Error parsing WS message: {e}")

            elif msg.type == WSMsgType.ERROR:
                ws_logger.error(f'ws connection closed with exception {ws.exception()}')

    finally:
        clients.remove(ws)
        # Remove from channels if present
        for ch, socket in list(channels.items()):
            if socket == ws:
                del channels[ch]
        ws_logger.info('Websocket connection closed')

    return ws

async def watchdog_loop():
    while True:
        await asyncio.sleep(60)
        await check_timeout()

def setup_routes(app):
    app.router.add_get('/', index)
    # Check if web directory exists
    if os.path.exists('./web'):
         app.router.add_static('/WEB/', path='./web/', name='web')
    else:
         logger.warning("Web directory not found. Static files will not be served.")

    app.router.add_get('/REST', handle_rest)
    app.router.add_get('/submit', handle_submit)

    # Consolidated handler for /app/{key}
    app.router.add_get('/app/{key}', app_handler)

async def start_web_server():
    app = web.Application()
    setup_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', settings.port)
    await site.start()
    logger.info(f"Web server started on port {settings.port}")
    return runner, app

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_web_server())
    loop.run_forever()
