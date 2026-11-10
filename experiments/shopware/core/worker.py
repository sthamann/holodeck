"""One process per event, including checkpoint reload and durable-state restore."""
import json,sys
from vm import Store, Model

request=json.load(sys.stdin)
store=Store(request['database'])
try:
    model=Model(request['checkpoint'])
    receipt=store.apply(request['tenant'],request['event'],model,request['revision'])
    print(json.dumps({'receipt':receipt,'graph':store.read(request['tenant'],request['event']['root'])}))
finally: store.close()
