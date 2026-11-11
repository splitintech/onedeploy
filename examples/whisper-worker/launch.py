"""Discover image-provided CUDA libraries before the dynamic loader initializes.

This launcher never downloads dependencies. --smoke checks shared-library loading
and Python imports without allocating a GPU or claiming inference qualification.
"""
import ctypes
import json
import os
from pathlib import Path
import site
import sys
import sysconfig


def library_dirs(roots=None):
    if roots is None:
        # Interpreter installation metadata, not a hardcoded Python/Conda path.
        roots = [Path(__file__).resolve().parent / 'deps']
        roots += [Path(p) for p in site.getsitepackages()]
        paths = sysconfig.get_paths()
        roots += [Path(paths[k]) for k in ('purelib', 'platlib') if paths.get(k)]
    found = []
    for root in roots:
        nvidia = Path(root) / 'nvidia'
        # Include transitive CUDA dependencies (runtime, NVRTC, nvJitLink, etc.)
        # from the same trusted installation; cuBLAS/cuDNN take precedence.
        packages = ['cublas', 'cudnn']
        if nvidia.is_dir():
            packages += sorted(p.name for p in nvidia.iterdir() if p.is_dir() and p.name not in packages)
        for package in packages:
            candidate = nvidia / package / 'lib'
            if candidate.is_dir():
                resolved = str(candidate.resolve())
                if resolved not in found:
                    found.append(resolved)
    return found


def loader_environment(directories, original=None):
    env = dict(os.environ if original is None else original)
    # Image-provided directories precede inherited system/vendor library paths.
    entries = list(directories)
    entries += [p for p in env.get('LD_LIBRARY_PATH', '').split(':') if p]
    env['LD_LIBRARY_PATH'] = ':'.join(dict.fromkeys(entries))
    return env


def smoke():
    # SONAMEs match the required CUDA12/cuDNN9 ABI. Resolving through the loader
    # also permits bases that install these libraries system-wide rather than pip.
    for library in ('libcublasLt.so.12', 'libcublas.so.12', 'libcudnn.so.9'):
        ctypes.CDLL(library, mode=ctypes.RTLD_GLOBAL)
    import ctranslate2
    from faster_whisper import WhisperModel  # noqa: F401
    print(json.dumps({'shared_libraries_loaded': True, 'runtime_imported': True,
                      'ctranslate2': ctranslate2.__version__, 'gpu_qualified': False}))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ['--smoke-child']:
        smoke()
        return
    if argv not in ([], ['--smoke']):
        raise SystemExit('Usage: launch.py [--smoke]')
    directory = Path(__file__).resolve().parent
    target = [str(directory / 'launch.py'), '--smoke-child'] if argv else [str(directory / 'worker.py')]
    # LD_LIBRARY_PATH is read at process start. Changing os.environ in the worker
    # immediately before importing CTranslate2 is not sufficient on Linux.
    os.execve(sys.executable, [sys.executable, *target], loader_environment(library_dirs()))


if __name__ == '__main__':
    main()
