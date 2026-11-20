import asyncio
import os
import signal
import sys

import pytest

from dc_manager.__main__ import install_shutdown_handler


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 不支援 SIGTERM handler")
async def test_sigterm_cancels_main_task_and_runs_cleanup():
    cleaned = []

    async def fake_run():
        install_shutdown_handler()
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.sleep(5)
        finally:
            cleaned.append(True)  # 相當於 async with bot 的關閉流程

    task = asyncio.create_task(fake_run())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned == [True]
    asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM)


def test_install_shutdown_handler_is_safe_without_signal_support():
    # Windows 或非主執行緒都不應該讓啟動失敗
    async def main():
        install_shutdown_handler()

    asyncio.run(main())
