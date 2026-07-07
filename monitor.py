"""Ad-hoc live monitor: hold one connection open and print every inbound frame."""

import asyncio
import time

from edifier_es300 import ES300, Status

HOST = "192.168.1.34"
PORT = 8080


def ts() -> str:
    return time.strftime("%H:%M:%S")


async def main() -> None:
    async with ES300(HOST, PORT) as device:
        # Prime the stream with a status request so we get an immediate baseline.
        await device._send_raw(
            {"id": str(int(time.time() * 1000)), "payload": "status_query"}
        )
        last_keepalive = asyncio.get_running_loop().time()
        last_status = None
        print("%s  connected, watching for changes..." % ts(), flush=True)
        while True:
            async for frame in device._frames(3.0):
                payload = frame.get("payload")
                if payload == "status_query":
                    status = Status.from_frame(frame)
                    summary = str(status)
                    if summary != last_status:
                        print("%s  CHANGE\n%s\n" % (ts(), summary), flush=True)
                        last_status = summary
                elif payload != "heart_beat":
                    print("%s  %-12s %s" % (ts(), payload, frame), flush=True)
            # Send a keepalive roughly every 3s so the device doesn't drop us.
            now = asyncio.get_running_loop().time()
            if now - last_keepalive >= 3.0:
                await device._send_raw(
                    {"id": str(int(time.time() * 1000)), "payload": "status_query"}
                )
                last_keepalive = now


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
