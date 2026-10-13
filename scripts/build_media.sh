#!/bin/sh
set -eu
project_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
build_target="$1"
build_dir="$2"
mkdir -p "$build_dir"
build_dir="$(CDPATH= cd -- "$build_dir" && pwd)"
"${PUBLISH_ASSISTANT_PYTHON:-python3}" "$project_root/scripts/unpack_media.py" "$build_dir/sources"
mkdir -p "$build_dir/prefix" "$build_dir/tools"
cd "$build_dir/sources/ffmpeg-9.0.1"
if [ "$build_target" = windows ]; then
 cd "$build_dir/sources/zlib-1.3.2"
 make -f win32/Makefile.gcc PREFIX=x86_64-w64-mingw32- -j4 libz.a
 cp libz.a "$build_dir/prefix/"
 cp zlib.h zconf.h "$build_dir/prefix/"
 cd "$build_dir/sources/ffmpeg-9.0.1"
 ./configure --target-os=mingw32 --arch=x86_64 --enable-cross-compile --cross-prefix=x86_64-w64-mingw32- --disable-shared --enable-static --disable-autodetect --disable-network --disable-ffplay --disable-doc --disable-debug --enable-zlib --extra-cflags="-I$build_dir/prefix" --extra-ldflags="-L$build_dir/prefix -static -static-libgcc" --extra-libs=-lz
 make -j4
 cp ffmpeg.exe ffprobe.exe "$build_dir/tools/"
else
 ./configure --disable-shared --enable-static --disable-autodetect --disable-network --disable-ffplay --disable-doc --disable-debug --enable-zlib
 make -j4
 cp ffmpeg ffprobe "$build_dir/tools/"
fi
cp COPYING.LGPLv2.1 "$build_dir/tools/FFmpeg-LICENSE.txt"
cp config.h ffbuild/config.mak "$build_dir/tools/"
cp "$project_root/Vendor/MediaSources/sources.json" "$build_dir/tools/source-components.json"
