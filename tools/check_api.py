import json
import urllib.request
import urllib.error
import sys

BASE = 'http://127.0.0.1:8000'

def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=5) as resp:
            return resp.getcode(), json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8')
    except Exception as e:
        return None, str(e)


def post(path, payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(BASE + path, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8')
    except Exception as e:
        return None, str(e)


if __name__ == '__main__':
    paths = ['/api/status', '/api/modes']
    for p in paths:
        code, body = get(p)
        print('GET', p, '->', code)
        print(json.dumps(body, indent=2) if isinstance(body, (dict, list)) else body)
        print('---')

    code, body = post('/api/chat', {'message': 'hello', 'mode': 'ai'})
    print('POST /api/chat ->', code)
    print(json.dumps(body, indent=2) if isinstance(body, (dict, list)) else body)
    print('---')

    code, body = post('/api/mode', {'mode': 'dev'})
    print('POST /api/mode ->', code)
    print(json.dumps(body, indent=2) if isinstance(body, (dict, list)) else body)
    print('---')
