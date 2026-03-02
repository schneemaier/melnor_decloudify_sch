import os
import json
import base64
import struct
import logging
import asyncio
#from calendar import weekday
from datetime import datetime
#import sys

# The melnor client adds 22 extra bytes to the submit messages, which breaks the standard aiohttp decoder
# This flag enables the use of a less restrictiveó, but slover decoder
os.environ['AIOHTTP_NO_EXTENSIONS'] = '1'

from aiohttp import web, WSMsgType
from settings import settings
from valveSettings import valveSettings

# probably no longer needed
import socketio

logger = logging.getLogger("WEB")
ws_logger = logging .getLogger("WS")
rest_logger = logging.getLogger("REST")

# Global state
# needs update to multi controller support
valve = [0] * 8
reported_valve = [0] * 8
valves = {}
reported_valves = {}
time_stamp = {}
remote_stamp = {}
connection_state = {}
battery_percent = {}
sm = {}
iv = {}

bin_fields = {
    'CONTROLLER_ID': 0,
    'DAY': 6,
    'TIME_LOW': 8,
    'TIME_HIGH': 9,
    'UNIT_ID_LOW_1': 10,
    'UNIT_ID_HIGH_1': 11,
    'BUTTONS_1': 12,
    'BATTERY_1': 13,
    'WATER_SENSOR_1': 14,
    'STATE_1': 15, # Valve unit connected?
    'UNIT_ID_LOW_2': 16,
    'UNIT_ID_HIGH_2': 17,
    'BUTTONS_2': 18,
    'BATTERY_2': 19,
    'WATER_SENSOR_2': 20,
    'STATE_2': 21  # Valve unit connected?
}

states = {
    0: 'online',
    1: 'just offline',
    2: 'offline for more than 5 minutes'
}

# WebSocket Clients
clients = set()
channels = {} # Map channel (MAC) to websocket connection
ws_connected = {} #Map channek (MAC) to connection stattus
hashkey = {} #Map channel (MAC) to hash keys
online = {} #Map channel (MAC) to status

# --- Helper Functions ---

def update_states(bin_state, remote_id):
    global remote_stamp, time_stamp, battery_percent, connection_state, reported_valves,reported_valve
    battery = {}
    connection = {}
    button = [0] * 2
    unit = []

    remote_stamp[remote_id] = bin_state[bin_fields['TIME_LOW']] + (bin_state[bin_fields['TIME_HIGH']] * 256)
    time_stamp[remote_id] = remote_stamp[remote_id]
    unit[1] = bin_state[bin_fields['UNIT_ID_HIGH_1']].hex() + bin_state[bin_fields['UNIT_ID_LOW_1']].hex()
    unit[2] = bin_state[bin_fields['UNIT_ID_HIGH_2']].hex() + bin_state[bin_fields['UNIT_ID_LOW_2']].hex()
    battery1 = bin_state[bin_fields['BATTERY_1']]
    battery2 = bin_state[bin_fields['BATTERY_2']]
    battery[unit1] = battery1 * 1.4428 - 268
    battery[unit2] = battery2 * 1.4428 - 268
    battery_percent[remote_id] = battery
    connection[unit1] = bin_state[bin_fields['STATE_1']]
    connection[unit2] = bin_state[bin_fields['STATE_2']]
    connection_state[remote_id] = connection
    button[1] = bin_state[bin_fields['BUTTONS_1']]
    button[2] = bin_state[bin_fields['BUTTONS_2']]

    logger.info(f"Batteries are roughly at {int(battery_percent[remote_id])}%")
    for b in range(2):
        for i in range(8):
            reported_valve[i] = button[b] & (2 ** i)
        valves[unit[b]] = reported_valve

    logger.info(f"BUTTONS: {reported_valves[remote_id]}")

async def send_message(event, data, channel_id=None):
    global channels
    if channel_id is None:
        ws_logger.debug(f"Channel is none")
        #channel_id = settings.mac1.lower()
    ws_logger.debug(f"Channel {channel_id}")
    if channel_id and channel_id in channels:
        payload = json.dumps({
            'event': event,
            'data': str(data),
            'channel': channel_id
        }, separators=(',', ':'))
        ws_client = channels[channel_id]
        if not ws_client.closed:
            ws_logger.debug(f"Sending message: {event} to channel {channel_id}")
            await ws_client.send_str(payload)
    else:
        payload1 = json.dumps({
            'event': event,
            'data': str(data)
        }, separators=(',', ':'))
        ws_logger.debug(f"Test printout {payload1}")
        for ws_client in clients: # needs update as it fails in multiple client case
            if not ws_client.closed:
                ws_logger.debug(f"Sending message: {event} to broadcast")
                await ws_client.send_str(payload1)

async def send_raw_message(msg):
    channel_id = settings.mac1.lower() #test
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
        ws_logger.debug(f"Channel is none")
        # channel_id = settings.mac1.lower()

    buffer = bytearray(134)
    try:
        valve_id = int(settings.valveId11, 16)
    except ValueError:
        logger.error(f"Invalid valveId in settings: {settings.valveId11}. Using 0.")
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
    }, separators=(',', ':'))

    if channel_id in channels:
        ws_client = channels[channel_id]
        if not ws_client.closed:
             await ws_client.send_str(payload)
    else:
        for ws_client in clients:
            if not ws_client.closed:
                await ws_client.send_str(payload)


async def msg_manual_sched(channel_arg, valveUnit=None):
    logger.info(f"Manual schedule for {channel_arg}")
    dbg = ''

    buffer = bytearray(20)
    try:
        valve_id = int(settings.valveId11, 16)
    except ValueError:
        logger.error(f"Invalid valveId in settings: {settings.valveId11}. Using 0.")
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
        'channel': settings.mac1.lower()
    }

    ws_logger.debug(f"Constructed msg : {json.dumps(ev, separators=(',', ':'))}")
    await send_raw_message(json.dumps(ev, separators=(',', ':')))
    return True

async def msg_sched_day(day, channel):
    global channels
    # read and send the program to the controller
    buffer = bytearray(308) # 2 * ( 2bytes valve ID + 4 * 38 bytes for valve schedule)
    if channel in valveSettings.valveUnits:
        unit = 0
        for vUnit in valveSettings.valveUnits[channel]:
            # Write valveId (2 bytes LE)
            struct.pack_into('<H', buffer, 0 + unit * 154, int(vUnit, 16))
            # Write data (2 bytes LE) at offset 4
            for v in range(0, 4):
                #4 valves
                for c in range(0, 6):
                    sta = int(valveSettings.schedule[vUnit][day][v][c]["start"])
                    sto = int(valveSettings.schedule[vUnit][day][v][c]["stop"])
                    eon = int(valveSettings.schedule[vUnit][day][v][c]["ecoOn"])
                    eof = int(valveSettings.schedule[vUnit][day][v][c]["ecoOff"])
                    #logger.info(f"Prog: {v}, {c}, {sta}, {sto}, {eon}, {eof}")
                    struct.pack_into('<H', buffer, 0 + unit * 154 + 2 + v * 38 +c * 6, sta)
                    struct.pack_into('<H', buffer, 0 + unit * 154 + 4 + v * 38 +c * 6, sto)
                    struct.pack_into('<H', buffer, 0 + unit * 154 + 6 + v * 38 +c * 6, eon)
                    struct.pack_into('<H', buffer, 0 + unit * 154 + 7 + v * 38 +c * 6, eof)
            unit += 1
    b64_data = base64.b64encode(buffer).decode('utf-8')
    #logger.info(f"String: {buffer.hex(' ')}")
    #logger.info(f"Base64 string: {b64_data}")
    payload = json.dumps({
        'event': f"sched_day{day}",
        'data': b64_data,
        'channel': channel
    }, separators=(',', ':'))

    if channel in channels:
        ws_client = channels[channel]
        if not ws_client.closed:
             await ws_client.send_str(payload)
    #await send_long_message(f"sched_day{day}", payload, channel)

#async def msg_timestamp(time, extra=0, channel=None):
async def msg_timestamp(minutes_of_day, day_of_week, channel=None):
    if channel is not None:
        b = bytearray(3)
        struct.pack_into('<H', b, 0, int(minutes_of_day))
        #struct.pack_into('b', b, 2, 0)
        struct.pack_into('b', b, 2, day_of_week)
        logger.info(f"Timestamp for day: {day_of_week}, {minutes_of_day} Array: {b.hex(' ')}")

        await send_message('timestamp', base64.b64encode(b).decode('utf-8'), channel)

async def msg_hashkey(key, channel):
    await send_message('hash_key', f'"{key}"', channel)

async def msg_rev_req(channel):
    await send_message('rev_request', '', channel)

async def msg_connection_established(ws):
    payload1 = json.dumps({
        'event': 'pusher:connection_established',
        'data': '{"socket_id":"265216.826472"}'
    }, separators=(',', ':'))
    ws_logger.debug(f"Test printout {payload1}")
    if not ws.closed:
        ws_logger.debug(f"Sending message: {payload1} to broadcast")
        await ws.send_str(payload1)
    #await send_message('pusher:connection_established', '{"socket_id":"265216.826472"}')

async def check_timeout(remote_id):
    #need additon to enable multi controller
    global time_stamp, valves, valve
    #time_stamp += 1
    now = datetime.now()
    minutes_of_day = now.hour * 60 + now.minute
    time_stamp[remote_id] = int(minutes_of_day)
    logger.debug(f"Watchdog : time:{time_stamp[remote_id]}/{remote_stamp[remote_id]}")
    dbg = ''
    logger.info("valves next")
    logger.debug(f"valve : {valves[remote_id]}")
    for v in valves[remote_id]:
        logger.debug(f"valve : {v}")

    ###
    # for i in range(len(valve)):
    #    t = int(valve[i])
    #    logger.debug(f"i : {i}")
    #    if t > time_stamp[remote_id]:
    #         dbg += f"V{i}:{t - time_stamp[remote_id]} "
    #    else:
    #         dbg += f"V{i}:OFF "
    #         valve[i] = 0
    #logger.debug(f"VALVES : {dbg}")
    ###

# --- Handlers ---

async def index(request):
    return web.FileResponse('./web/index.html')

async def handle_rest(request):
    # needs complete overhaul!!!!
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
    logger.info(f"Submit.")
    id_hash = request.query.get('idhash', '').replace("'", "")
    message = request.query.get('message', '')

    bin_state = None
    logger.info(f"Device sent: {message}.")
    if message.endswith('ack--null'):
        ack_type = message.replace('ascii--', '').replace('--ack--null', '')
        bin_state = bytearray(4)
    elif message.startswith('ascii--re'):
        # handle revision message which otherwise creates a decode error
        ack_type = message.replace('ascii--', '')
        bin_state = bytearray(4)
    else:
        # Some padding might be needed for base64 decoding if not valid
        # Python's base64 module is strict about padding
        padded_message = message + '=' * (-len(message) % 4)
        try:
            bin_state = base64.b64decode(padded_message)
            logger.info(f"Binstate: {bin_state.hex(' ')}")
        except Exception as e:
            logger.error(f"Error decoding base64 message: {e}")
            return web.Response(text='OK')

    # Parse remoteId (MAC address)
    if len(bin_state) >= 6:
        remote_id = ''.join(f'{b:02x}' for b in reversed(bin_state[0:6]))
    else:
        remote_id = id_hash[:12] # use the hash if message is ascii--hashkeyevnt--ack--null otherwise cycle fails

    if len(bin_state) >= 10:
        remote_stamp[remote_id] = bin_state[8] + (bin_state[9] * 256)
        # update for multi controller
        time_stamp[remote_id] = remote_stamp[remote_id]
        logger.info(f"Time update from {remote_id}, time {remote_stamp[remote_id]}")

    # First message from device check (id_hash is checked against '0000000000' etc)
    if id_hash == '0000000000' or id_hash == 'ffffffffff':
        logger.info(f"Received submit for channel {remote_id}, sending hashkey")
        await msg_hashkey(remote_id, remote_id)
        hashkey[remote_id] = remote_id
        # setup state machine
        sm[remote_id] = 0
        return web.Response(text='OK')

    if remote_id not in ws_connected  or not ws_connected[remote_id]:
        ws_logger.error('Device not in sync. Please reset or wait.')
        ws_logger.error(f"remoteid: {remote_id}, WS_connected: {ws_connected}")
        sm[remote_id] = 0
        return web.Response(text='OK')

    # State Machine
    # change state macine to dictionary to make it remote id dependent

    if sm[remote_id] < 7:
        await msg_sched_day(sm[remote_id], remote_id)
        sm[remote_id] += 1
        return web.Response(text='OK')

    #if sm[remote_id] == 7:
    #    # add remote id
    #    await msg_manual_sched(remote_id)
    #    sm[remote_id] += 1
    #    return web.Response(text='OK')


    if sm[remote_id] == 7:
        #while datetime.now().second > 5:
        #    logger.debug(f'Waiting for time: {datetime.now().second}')
        #    await asyncio.sleep(1)
        now = datetime.now()
        minutes_of_day = now.hour * 60 + now.minute
        #await msg_timestamp(time_stamp, 0x03, remote_id)
        await msg_timestamp(minutes_of_day, now.weekday(), remote_id)
        sm[remote_id] += 1
        return web.Response(text='OK')

    if sm[remote_id] == 8:
        await msg_rev_req(remote_id)
        sm[remote_id] += 1
        return web.Response(text='OK')

    if sm[remote_id] == 9:
        # iv has to be per remote id
        if remote_id in iv:
            iv[remote_id].cancel()
        # Start watchdog loop
        iv[remote_id] = asyncio.create_task(watchdog_loop(remote_id))
        sm[remote_id] += 1

    #if message.startswith('ascii--revisions--E400'):
    if message.startswith('ascii--revisions--'):
        logger.debug(f'Device sent revisions: {ack_type[11:]}')
        return web.Response(text='OK')

    if message.startswith('ascii--timestampevnt--'):
        logger.debug('Device sent timestampevnt ack')
        return web.Response(text='OK')

    # Need fix multi mac address
    #if remote_id == 'ffffffffffff' or remote_id == settings.mac1.lower():
    logger.info(f"remoteID ({remote_id})")
    if remote_id in valveSettings.controllerMac:
        logger.info(f"remoteID ({remote_id}) in controllerMac")
        update_states(bin_state, remote_id) # update to multi device
        if connection_state[remote_id] == 0:
            online[remote_id] = True #need to change to support multi device
            logger.info(f"Device online ({remote_id})")
        else:
            logger.info(f"Device not online ({remote_id})")
    elif remote_id == '000000000000':
        pass
    else:
        online[remote_id] = True
        logger.info(f"Device in unknown state {remote_id}")

    return web.Response(text='OK')

async def app_handler(request):
    """
    Handles requests to /app/{key}.
    If it's a WebSocket upgrade request, it initiates the WebSocket connection.
    Otherwise, it returns a standard OK response.
    """
    global ws_connected, online, sm
    logger.debug(f"New Pusher client request header: {request.headers}")
    if request.headers.get('Upgrade', '').lower() == 'websocket':
        logger.debug(f"WebSocket upgrade request from : {request.remote}")
        port = request.transport.get_extra_info('peername')[1]
        ws_logger.debug(f"New WS connection established from port id {port}")
        return await websocket_handler(request) #web.Response(text='OK') #websocket_handler(request)
    else:
        #rest_logger.debug(f"New Pusher client connected: {request.query}")
        logger.debug(f"New Pusher client connected: {request.query}")
        return web.Response(text='OK')

async def websocket_handler(request):
    global ws_connected, online, sm
    ws = web.WebSocketResponse(heartbeat=90.0)
    await ws.prepare(request)

    clients.add(ws)

    port = request.transport.get_extra_info('peername')[1]
    ws_logger.debug(f"New WS connection established from port id {port}")

    await msg_connection_established(ws)
    try:
        ws_logger.debug(f"Starting Try")
        ws_logger.debug(f"WS Message: {ws}")
        async for msg in ws:
            ws_logger.debug(f"Message type {msg.type}")
            if msg.type == WSMsgType.TEXT:
                try:
                    ws_logger.debug(f"Message type is text")
                    data = json.loads(msg.data)
                    ws_logger.debug(f"Message is: {data}")
                    ws_logger.debug(f"New WS event {data.get('event')} for portid {port}")

                    if data.get('event') == 'pusher:ping':
                        ws_logger.info(f'Received pusher ping on client {data}')
                        channel_name = data.get('data', {}).get('channel')
                        await send_message('pusher:pong', '{}', channel_name) #settings.mac1.lower())

                    elif data.get('event') == 'pusher:subscribe':
                        channel_name = data.get('data', {}).get('channel')
                        ws_logger.info(f"Received subscribe request for channel {channel_name}")
                        channels[channel_name] = ws
                        ws_connected[channel_name] = True

                        await send_message('pusher_internal:subscription_succeeded', '{}', channel_name) #settings.mac1.lower())
                        online[channel_name] = False
                        sm[channel_name] = 0

                    else:
                        ws_logger.error('I dont know how to handle this event.')

                except Exception as e:
                    ws_logger.error(f"Error parsing WS message: {e}")

            elif msg.type == WSMsgType.ERROR:
                ws_logger.error(f'ws connection closed with exception {ws.exception()}')

    finally:
        ws_logger.debug(f"Try Failed....")
        clients.remove(ws)
        # Remove from channels if present
        for ch, socket in list(channels.items()):
            if socket == ws:
                del channels[ch]
        ws_logger.info('Websocket connection closed')

    return ws

async def watchdog_loop(remote_id):
    while True:
        await asyncio.sleep(60)
        await check_timeout(remote_id)

def setup_routes(app):
    app.router.add_get('/', index)
    # Check if web directory exists
    if os.path.exists('./web'):
         app.router.add_static('/WEB/', path='./web/', name='web')
    else:
         logger.warning("Web directory not found. Static files will not be served.")

    app.router.add_get('/REST', handle_rest)
    app.router.add_get('/submit/', handle_submit)

    # Consolidated handler for /app/{key}
    app.router.add_get('/app/{key}', app_handler)

    # Handle Websocket
    app.router.add_get('/ws', websocket_handler)

async def start_web_server():
    # 1. Initialize the Socket.IO async serveruld be given to the Treasurer, Lee Carmon (principal flute)
    # async_mode='aiohttp' ensures compatibility with the aiohttp framework
    #sio = socketio.AsyncServer(async_mode='aiohttp', cors_allowed_origins='*')
    app = web.Application()
    # 2. Attach the Socket.IO server to the aiohttp application
    #sio.attach(app)

    setup_routes(app)
    runner = web.AppRunner(app, access_log=logging.getLogger('aiohttp.access'))
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
