#!/usr/bin/env python3
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "frontend" / "src"

def check_file(path: Path) -> list[str]:
    errors = []
    content = path.read_text(encoding="utf-8")
    
    # Check 1: Trailing content after final </style>
    style_close = content.rfind("</style>")
    if style_close != -1:
        trailing = content[style_close + len("</style>"):].strip()
        if trailing:
            errors.append(f"Content found after closing </style> tag: {trailing[:60]}...")
            
    # Check 2: Check for unclosed or mismatched tags
    template_open = len(re.findall(r"<template\b", content))
    template_close = len(re.findall(r"</template>", content))
    if template_open != template_close:
        errors.append(f"Mismatched <template> tags: {template_open} open vs {template_close} close")

    script_open = len(re.findall(r"<script\b", content))
    script_close = len(re.findall(r"</script>", content))
    if script_open != script_close:
        errors.append(f"Mismatched <script> tags: {script_open} open vs {script_close} close")

    style_open = len(re.findall(r"<style\b", content))
    style_close_count = len(re.findall(r"</style>", content))
    if style_open != style_close_count:
        errors.append(f"Mismatched <style> tags: {style_open} open vs {style_close_count} close")

    return errors

def main():
    failed = False
    for vue_file in ROOT.rglob("*.vue"):
        errs = check_file(vue_file)
        if errs:
            failed = True
            print(f"❌ {vue_file.relative_to(ROOT.parent)}:", file=sys.stderr)
            for e in errs:
                print(f"   - {e}", file=sys.stderr)
                
    if failed:
        print("\nSFC verification failed! Please fix the errors above.", file=sys.stderr)
        sys.exit(1)
    else:
        print("✅ All Vue Single File Components passed structural verification.")

if __name__ == "__main__":
    main()
