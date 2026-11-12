"""Intear `dex_events_borsh` event from mainnet: launch pool 304 of e-1.launch-v2.intear.near
(tx 5pytvG8fcJMPLa8BMYEi8nhzijejyGjhxw6Pa9T2hCB9, block 222713106)."""
import json

LAUNCH_EVENT = ("7AAAAPAaAQAAABAAAABzbGltZWRyYWdvbi5uZWFyAwAAAHh5a64AAAAAMAEAAAYJAICseCZYlngn+AwA8QsAARkAAABlLTEubGF1bmN"
                "oLXYyLmludGVhckAAACMA/wIKW8E4k41Exk0xAAACAAAAA2kAAbABACdwAgAECZwAAIwADyQABPAHAcBmbrmrNeQYQA0DAMAGJ+rxOOQY6KU"
                "ADJcAawABFQAAAJQAYHIubmVhcg==")


def borsh_event():
    return "EVENT_JSON:" + json.dumps({"standard": "inteardex", "version": "1.0.0", "event": "dex_events_borsh",
                                       "data": LAUNCH_EVENT})
