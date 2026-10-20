"""Frozen backend/runner executable used by both desktop packages."""
import asyncio
import json
import os
import sys

for stream in (sys.stdout, sys.stderr):
    if stream is not None and hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)


def main():
    action = sys.argv.pop(1) if len(sys.argv) > 1 else 'server'
    if action == 'runner':
        from draft_execution import main as run
        asyncio.run(run())
    elif action == 'server':
        from pathlib import Path
        private = Path(os.environ['SAU_WORKBENCH_PRIVATE'])
        private.mkdir(parents=True, exist_ok=True)
        (private / 'backend-owner.json').write_text(json.dumps({'pid': os.getpid(), 'executable': sys.executable}), encoding='utf-8')
        from draft_workbench import main as serve
        serve()
    elif action == 'self-test':
        from runtime_support import media_tool
        from material_probe import media_info
        from patchright.async_api import async_playwright
        from PIL import Image
        import draft_workbench
        import draft_execution
        from uploader.douyin_uploader.main import DouYinVideo
        from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo
        from uploader.ks_uploader.main import KSVideo
        from uploader.tencent_uploader.main import TencentVideo
        from uploader import upload_pipeline, publication_workflow, xhs_publication_readback, tc_reuse, tc_stage, ks_reopened_assets, tc_sdk_evidence, tc_reopen_mapping, tc_preview_evidence, tc_cover_derivative
        print(json.dumps({'app': 'publish-assistant', 'frozen': bool(getattr(sys, 'frozen', False)),
                          'platform': sys.platform, 'tools': {n: media_tool(n) for n in ('ffmpeg', 'ffprobe')},
                          'imports': True}))
    else:
        raise SystemExit('Unknown backend operation')


if __name__ == '__main__':
    main()
