# BP Cloner

Drives the BP Cloner capture app on this Mac: builds a chain of plugins (named as AiAudioHub's plug-in library lists them), sets their parameters, runs captures and reads their fidelity results. It only talks to the BP Cloner app on this same Mac, through its local control port (TCP 47811 on 127.0.0.1), so it needs 'send to the local network' for that one port.

An add-on for AiAudioHub: get it in AiAudioHub, Explore. Namespace `jpmm-bp-cloner`, version 0.1.0.
