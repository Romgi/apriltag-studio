"""Package a built Windows app with source, docs, assets, and license notices."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    'app.py', 'engine.py', 'calibration.py', 'pose_geometry.py', 'pose_panel.py',
    'pose_view.py', 'Build.ps1', 'requirements.txt', 'requirements-dev.txt',
    'Start AprilTag Studio.cmd', 'Start Demo.cmd', 'Start 3D Example.cmd',
    'README.md', 'LICENSE', 'VERSION', 'CHANGELOG.md', 'CONTRIBUTING.md',
    'THIRD_PARTY_NOTICES.md', 'VALIDATION.md',
)
SOURCE_DIRECTORIES = ('assets', 'docs', 'licenses', 'scripts', 'tests')


def package(version: str, portable: Path, output: Path) -> Path:
    if not re.fullmatch(r'[0-9][0-9A-Za-z.+_-]*', version):
        raise ValueError('Use a version such as 0.1.0 or 0.2.0-beta.1.')
    portable = portable.resolve()
    if not (portable/'AprilTagStudio.exe').is_file() or not (portable/'_internal').is_dir():
        raise ValueError('Build the portable application first with Build.ps1.')
    files = [(ROOT/name, name) for name in ROOT_FILES]
    for name in SOURCE_DIRECTORIES:
        directory = ROOT/name
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        files.extend((path, path.relative_to(ROOT).as_posix())
                     for path in sorted(directory.rglob('*')) if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix not in ('.pyc','.pyo'))
    files.extend((path, 'portable/'+path.relative_to(portable).as_posix())
                 for path in sorted(portable.rglob('*')) if path.is_file()
                 and path.suffix not in ('.log',))
    for path,_ in files:
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.is_symlink():
            raise ValueError(f'Release input must not be a symbolic link: {path.name}')
    output.mkdir(parents=True, exist_ok=True)
    name = f'AprilTagStudio-v{version}-windows-x64'
    target = output/(name+'.zip')
    temporary = output/(name+'.zip.partial')
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path,relative in files:
            archive.write(path,name+'/'+relative)
    temporary.replace(target)
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream,'sha256').hexdigest()
    target.with_suffix('.zip.sha256').write_text(f'{digest}  {target.name}\n',encoding='ascii')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',default=(ROOT/'VERSION').read_text().strip())
    parser.add_argument('--portable-directory',type=Path,default=ROOT/'portable')
    parser.add_argument('--output-directory',type=Path,default=ROOT/'dist')
    args = parser.parse_args()
    print(package(args.version,args.portable_directory,args.output_directory).resolve())
