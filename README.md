> [!IMPORTANT]
> This repository is the [`janrysavy/PyPC-MPC`](https://github.com/janrysavy/PyPC-MPC)
> fork focused on AI-assisted reverse engineering of DOS software. Its default
> `ai-re-agent` branch adds a supervised JSON-RPC debugger agent.
>
> **Top methods:** `agent.capabilities`; `emulator.info`; `session.status`;
> `execution.pause/continue/go/step`; register and memory access; keyboard input
> and state; I/O reads; CGA text, attributes, and video snapshots. See the
> [complete JSON-RPC API specification](JSON_RPC_API.md) for exact request,
> response, state, safety, and framing semantics.
>
> The current protocol is JSON-RPC 2.0 over a local TCP socket at
> `127.0.0.1:2301` using JSON-lines framing; despite the repository name, it is
> not yet a Model Context Protocol server.

PyPC is an IBM PC (8088) emulator written in Python.

It boots and you can run e.g. MS-DOS with CheckIt3 or SpaceQuest 3 in it.
You need to have 'GLABIOS.ROM' in the directory from which you start 'main.py'. It requires python 3.12 or later.
When it is running, you can also connect a VNC client to it: you see it then use the original font and see nice graphics.


### via telnet
![main screen via telnet](images/pypc001.png)

### via vnc
![main screen via VNC](images/pypc002.png)

### spacequest 3 via vnc
![main screen via VNC](images/pypc003.png)


Note that this is a manual translation of https://github.com/folkertvanheusden/Dotxt

If you want a quicker version, run it from 'pypy'. For that you may need to invoke this first on the source code of PyPC:

```shell
sed -i 's/import override,/import /g' *py
sed -i 's/@override//g' *py
```

Folkert van Heusden

released under MIT license
