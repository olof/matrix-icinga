#!/usr/bin/python3
import argparse
import asyncio
import configparser
import json
import os
import sys

from nio import AsyncClient, ClientConfig, RoomMessageText, LoginResponse

__version__ = '0.1'

argparser = argparse.ArgumentParser()
argparser.add_argument(
    '-V', '--version',
    action='version', version='%%(prog)s %s' % __version__
)
argparser.add_argument(
    '-f', '--config',
    default='/etc/icinga2/matrix.ini',
    help='configuration file',
)
argparser.add_argument(
    '-t', '--type',
    help='Notification type',
)
argparser.add_argument(
    '-S', '--service',
    required=True,
    help='Service name of status message',
)
argparser.add_argument(
    '-H', '--host',
    help='Hostname of status message',
)
argparser.add_argument(
    '-s', '--state',
    help='Service state',
)
argparser.add_argument(
    '-o', '--output',
    help='Service output',
)
argparser.add_argument(
    '-m', '--message',
    help='Additional status comment',
)
argparser.add_argument(
    "-T", "--timeout",
    type=int, default=10,
    help="Matrix sync timeout in seconds (default 10s)",
)


def write_state(filename, state):
    with open(filename, 'w') as fh:
        json.dump({
            'access_token': state.access_token,
            'device_id': state.device_id,
            'user_id': state.user_id,
        }, fh)

def ensure_dir(name):
    try:
        os.mkdir(name)
    except FileExistsError:
        pass

state_colors = {
  'ok': '11cc11',
  'warning': 'cccc11',
  'critical': 'cc1111',
  'unknown': '1111cc'
}

state_colors_back = {
  'ok': '228822',
  'warning': '888822',
  'critical': '882222',
  'unknown': '222288'
}


async def main(args):
    cfgparser = configparser.ConfigParser()
    with open(args.config) as fh:
        cfgparser.read_file(fh)
    cfg = cfgparser['DEFAULT']
    room = cfg['room']

    payload = {
        'service': args.service,
        'host': args.host,
        'type': args.type.upper(),
        'state': args.state.upper(),
        'output': args.output,
        'msg': args.message,
    }

    device = {}
    device_state = os.path.join(cfg['state'], 'device.json')
    try:
        with open(device_state) as fh:
            device = json.load(fh)
    except FileNotFoundError:
        pass

    ensure_dir(cfg['state'])
    ensure_dir(os.path.join(cfg['state'], 'nio'))
    ensure_dir(os.path.join(cfg['state'], 'nio', cfg['user_id']))

    client = AsyncClient(
        cfg['homeserver'],
        cfg['user_id'],
        device_id=device.get('device_id'),
        store_path=os.path.join(cfg['state'], 'nio', cfg['user_id']),
        config=ClientConfig(store_sync_tokens=True)
    )

    if device:
        client.access_token = device['access_token']
        client.user_id = device['user_id']
        client.load_store()
    else:
        resp = await client.login_raw({
            'type': 'org.matrix.login.jwt',
            'token': cfg['token'],
        })
        if (isinstance(resp, LoginResponse)):
            write_state(device_state, resp)
        else:
            print(f"Failed to log in: {resp}", file=sys.stderr)
            sys.exit(1)

    await client.sync(timeout=args.timeout * 1000, full_state=True)

    in_rooms = client.rooms.keys()
    room_id = (await client.room_resolve_alias(room)).room_id
    if not room_id in in_rooms:
        await client.join(room_id)
    for unwanted in [r for r in in_rooms if r != room_id]:
        await client.room_leave(room)

    if client.should_upload_keys:
        await client.keys_upload()
    if client.should_query_keys:
        await client.keys_query()
    if client.should_claim_keys:
        await client.keys_claim(client.get_users_for_key_claiming())

    await client.sync(timeout=args.timeout * 1000, full_state=True)

    ps_l = []
    if payload['msg']:
        ps_l.append(payload['msg'])
    if payload['output']:
        ps_l.append(payload['output'])
    ps = '\n'.join(ps_l)

    await client.room_send(room_id, 'm.room.message', {
        'msgtype': 'm.text',
        'body': '{type}: {service} on {host} is {state}\n{msg}'.format(**payload),
        'format': 'org.matrix.custom.html',
        'formatted_body': '<span style="background-color: #{color};"><span data-mx-bg-color="#{color}"><strong>{type}</strong>:</span></span> <span data-mx-bg-color="#ffffff">Service <strong>{service}</strong> on <strong>{host}</strong> is <strong>{state}</strong>:<br />{ps}</span>'.format(**payload, bgcolor=state_colors_back.get(payload['state'].lower(), '222288'), ps=ps, color=state_colors.get(payload['state'].lower(), '1111ff')),
    }, ignore_unverified_devices=True)

    await client.sync(timeout=args.timeout * 1000, full_state=True)

    await client.close()


if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main(argparser.parse_args()))
