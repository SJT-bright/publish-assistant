"""CLI entrypoint for persistent native draft execution."""
import asyncio
from draft_execution import *

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        print('Task interrupted: ' + str(exc), flush=True)
        raise SystemExit(1)
