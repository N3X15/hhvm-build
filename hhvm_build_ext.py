#!/usr/bin/python
'''
A Crappy HHVM Compiler (extension builder)

run with ./hhvm_build_ext debian jessie hhvm_pgsql
'''
import os
import sys
import yaml
import tempfile
import re
import shutil
import logging
import subprocess
import datetime
import glob

#from hhvm_build import *

script_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(script_dir, 'lib', 'buildtools'))

from buildtools import *
from buildtools import os_utils
from buildtools.wrapper import CMake, FPM, configure_ccache, configure_cotire, configure_distcc
from buildtools.repo.git import GitRepository

class Extension:

  def __init__(self, _id):
    self.id = _id
    self.config = {}
    self.repo = None
    self.deployables = {}
    self.dir = os.path.join(script_dir, 'exts', self.id)
    self.repo_dir = os.path.join(self.dir, 'repo')
    self.patch_dir = os.path.join(self.dir, 'patches')
    self.branch = ''
    self.remote = ''

    self.LoadConfig()

  def LoadConfig(self):
    with log.info('Loading %s configuration...', self.id):
      self.config = Config(os.path.join(self.dir, 'config.yml'), {}, template_dir='/', variables={
          'SOURCE_DIR': SOURCE_DIR,
          'DISTRO_DIR': DISTRO_DIR,
          'INSTALL_DIR': INSTALL_DIR,
          'PACKAGE_DIR': PACKAGE_DIR,
          'DISTRO_DIR': DISTRO_DIR,
          # 'SKELETON_DIR': os.path.join(HHVMBUILD_DIR, 'hhvm', DISTRO_NAME, skeletondirname),
          # 'HHVMBUILD_DIR': HHVMBUILD_DIR,
          'ARCH': ARCH,
          'EXT_DIR': self.dir,
          'EXT_SOURCE_DIR': self.repo_dir,
      })
      repotype = self.config.get('repo.type')
      self.branch = self.config.get('repo.branch', 'master')
      self.remote = self.config.get('repo.remote')
      if repotype == 'git':
        self.repo = GitRepository(self.repo_dir, self.remote, quiet=False, noisy_clone=True)

      self.patches = self.config.get('patches', {})

  def build(self,clean=False):
    with Chdir(self.repo_dir):
      with log.info('Checking repo for updates...'):
        self.repo.quiet = False
        self.repo.CheckForUpdates(remote='origin', branch=self.branch, quiet=False)
        self.repo.Pull(remote='origin', branch=self.branch, cleanup=clean)
        self.repo.UpdateSubmodules()
      if len(self.patches) > 0:
        os_utils.ensureDirExists(self.patch_dir, quiet=False)
        with log.info('Applying patches...'):
          for patchID, patchURL in self.patches.items():
            patchPath = os.path.join(self.patch_dir, patchID + '.patch')
            http.DownloadFile(patchURL, patchPath)
            if not cmd(['git', 'apply', '--check', patchPath], echo=True, critical=True):
              sys.exit(1)
            if not cmd(['git', 'apply', patchPath], echo=True, critical=True):
              sys.exit(1)
      with log.info('Running hphpize...'):
        if not cmd([cfg.get('bin.hphpize', 'hphpize')], critical=True, echo=True):
          sys.exit(1)
      with log.info('Configuring...'):
        if not cmake.run(cfg.get('bin.cmake', 'cmake')):
          sys.exit(1)
      with log.info('Compiling...'):
        if not cmd([cfg.get('bin.make', 'make')] + MAKE_FLAGS, critical=True, echo=True):
          sys.exit(1)

if __name__ == '__main__':
  import argparse
  argp = argparse.ArgumentParser(prog='hhvm_build_ext', description='Build HHVM extension')

  argp.add_argument('distro', type=str, help='Linux Distribution (deb, etc)')
  argp.add_argument('release', type=str, help='OS Release codename (precise, etc)')

  argp.add_argument('extID', type=str, help='Extension\'s ID in config.yml')

  argp.add_argument('-c', '--config', type=str, default='config.yml', help='YAML file to read configuration from.')

  argp.add_argument('--disable-ccache', action='store_true')
  argp.add_argument('--disable-distcc', action='store_true')
  argp.add_argument('--disable-cotire', action='store_true')
  argp.add_argument('--package-only', action='store_true')
  argp.add_argument('--disable-packaging', action='store_true')
  argp.add_argument('--disable-repo-deploy', action='store_true')
  argp.add_argument('--disable-git-clean', action='store_true')
  argp.add_argument('--force-rebuild', action='store_true')

  args = argp.parse_args()

  cfg = Config(args.config, {})

  if args.disable_ccache:
    cfg['env']['ccache']['enable'] = False
  if args.disable_distcc:
    cfg['env']['distcc']['enable'] = False
  if args.disable_cotire:
    cfg['env']['cotire']['enable'] = False
  if args.disable_packaging:
    cfg['env']['packaging']['enable'] = False
  if args.disable_repo_deploy:
    cfg['env']['packaging']['repo-deploy'] = False

  DISTRO_NAME = args.distro
  DISTRO_RELEASE = args.release

  HHVMBUILD_DIR = os.getcwd()
  DISTRO_DIR = os.path.join(HHVMBUILD_DIR, 'hhvm', DISTRO_NAME, DISTRO_RELEASE)

  stdout, stderr = cmd_output('dpkg-architecture -qDEB_BUILD_GNU_TYPE'.split(), critical=True, echo=False)
  ARCH = (stdout + stderr).strip()
  log.info('Building with ARCH=%r', ARCH)

  # HHVM_VERSION = args.version
  SOURCE_DIR = os.path.abspath(cfg.get('paths.source'))
  INSTALL_DIR = os.path.abspath(cfg.get('paths.install'))
  PACKAGE_DIR = os.path.abspath(cfg.get('paths.package'))

  ext = Extension(args.extID)

  if not os.path.isdir(ext.dir):
    log.info('Extension source code not found. ext.dir={}'.format(ext.dir))
    sys.exit(1)

  if not os.path.isdir(SOURCE_DIR):
    log.info('HHVM source code not found. SOURCE_DIR={}'.format(SOURCE_DIR))
    sys.exit(1)

  ENV.merge({
      'CC': cfg.get('bin.cc', 'gcc-4.8'),
      'CXX': cfg.get('bin.cxx', 'g++-4.8'),
      'ASM': cfg.get('bin.asm', 'cc'),

      'CMAKE_INCLUDE_PATH': tempfile.mkstemp(),
      'CMAKE_LIBRARY_PATH': "/usr/lib/hhvm/",
      'HPHP_HOME': SOURCE_DIR
  })

  cmake = CMake()
  for k, v in cfg.get('env.cmake.flags', {}).items():
    cmake.setFlag(k, v)

  MAKE_FLAGS = cfg.get('env.make.flags', [])

  cmake.setFlag('CMAKE_BUILD_TYPE', 'Debug')
  cmake.setFlag('CMAKE_INSTALL_PREFIX', '/usr')

  configure_ccache(cfg, cmake)
  configure_distcc(cfg, cmake)
  configure_cotire(cfg, cmake)

  #job_flag = '-j' + str(cfg.get('env.make.jobs', 1))
  #MAKE_FLAGS += [job_flag]

  with log.info('Compiling extension...'):
    with log.info('%s...', ext.id):
      ext.build(clean=args.force_rebuild)
