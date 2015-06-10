#!/usr/bin/python
'''
A Crappy HHVM Compiler

run with ./hhvm_build debian jessie 3.3
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

script_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(script_dir, 'lib', 'buildtools'))

from buildtools import *
from buildtools import os_utils, http
from buildtools.wrapper import CMake, FPM, configure_ccache, configure_cotire, configure_distcc
from buildtools.repo.git import GitRepository
from buildtools.posix.elf import ELFInfo


def bool2yn(b):
  return 'Y' if b else 'N'


def cleanDir(dir):
  for the_file in os.listdir(dir):
    file_path = os.path.join(dir, the_file)
    try:
      if os.path.isfile(file_path):
        os.unlink(file_path)
      else:
        shutil.rmtree(file_path)
    except Exception, e:
      log.error(e)
      sys.exit(1)


def mkdirOrClear(dir):
  if not os.path.isdir(dir):
    log.info('Creating {}'.format(dir))
    os.makedirs(dir)
  else:
    log.info('Clearing %s', dir)
    cleanDir(dir)


def dictToTuples(inp):
  return [(k, v) for k, v in inp.items()]


def handleIf(operators, pkg_cfg, var_replacements):
  if len(operators):
    for expr in operators:
      (operator, args) = dictToTuples(expr)[0]
      if isinstance(args, str):
        args = args.split(' ')
      args = [replace_vars(arg, var_replacements) for arg in args]
      if operator == 'file-exists':
        if not os.path.exists(args[0]):
          return False
      else:
        log.warn('Invalid operator %s', operator)
    return True
  else:
    return False


def handleChdir(operators, pkg_cfg, var_replacements):
  if len(operators):
    origpath = os.path.abspath(os.getcwd())

    def jumpBack():
      log.info('cd %s', origpath)
      os.chdir(origpath)
    for expr in operators:
      (operator, args) = dictToTuples(expr)[0]
      if isinstance(args, str):
        args = args.split(' ')
      args = [replace_vars(arg, var_replacements) for arg in args]
      newdir = os.path.abspath(args[0])
      if operator == 'dir':
        if not os.path.isdir(newdir):
          log.warn('Directory %s does not exist, cannot chdir.', newdir)
          return (False, None)
        else:
          os.chdir(newdir)
          log.info('cd %s', newdir)
          return (True, jumpBack)
      else:
        log.warn('Invalid operator %s', operator)
    log.info('cd %s', args[0])
    return (True, jumpBack)
  else:
    return (False, None)


def RunCommandsIn(commandlist, pkg_cfg, var_replacements):
  if len(commandlist) == 0:
    return
  with log:
    for package_cmd in commandlist:
      # Conditionals
      if isinstance(package_cmd, dict):
        result = None
        postwork = None
        if 'if' in package_cmd:
          result = handleIf(package_cmd['if'], pkg_cfg, var_replacements)
        if 'if-not' in package_cmd:
          result = not handleIf(package_cmd['if-not'], pkg_cfg, var_replacements)
        if 'chdir' in package_cmd:
          result, origpath = handleChdir(package_cmd['chdir'], pkg_cfg, var_replacements)
        if result is None:
          continue
        RunCommandsIn(package_cmd.get('then' if result else 'else', []), pkg_cfg, var_replacements)
        if postwork is not None:
          origpath()
        continue

      # Strings -> lists
      if isinstance(package_cmd, str):
        package_cmd = package_cmd.split(' ')

      ccmd = [replace_vars(fragment, var_replacements) for fragment in package_cmd]
      command = ccmd[0]
      cmd(ccmd, echo=True, critical=True)


def aggregate(cfg, dir):
  job_cfg = yaml.load(os.path.join(dir, 'package.yml'))


def CloneOrPull(id, uri, dir):
  if not os.path.isdir(dir):
    cmd(['git', 'clone', uri, dir], echo=True, show_output=True, critical=True)
  else:
    with os_utils.Chdir(dir):
      cmd(['git', 'pull'], echo=True, show_output=True, critical=True)
  with os_utils.Chdir(dir):
    log.info('{} is now at commit {}.'.format(id, Git.GetCommit()))


if __name__ == '__main__':
  import argparse

  # logging.basicConfig(
  #    format='%(asctime)s [%(levelname)-8s]: %(message)s',
  #    datefmt='%m/%d/%Y %I:%M:%S %p',
  #    level=logging.INFO)
  #    # filename='logs/main.log',
  #    # filemode='w')

  # define a Handler which writes INFO messages or higher to the sys.stderr
  # console = logging.StreamHandler()
  # console.setLevel(logging.INFO)
  # logging.getLogger('').addHandler(console)

  # log = IndentLogger()

  d_cfg = {
      'env': {
          'distcc': {
              'enabled': False,
              'hosts': {}
          },
          'ccache': {
              'enabled': False
          },
          'cotire': {
              'enabled': False
          },
          'make': {
              'jobs': 5,
              'flags': []
          },
          'packaging': {
              'enabled': True,
              'repo-deploy': True,
              'maintainer': 'Rob Nelson <nexisentertainment@gmail.com>',
              'packages': {
                  'debian/jessie': True,
                  'debian/wheezy': False
              },
          }
      },
      'bin': {
          'make': 'make',
          'pump': 'distcc-pump',
          'ccache': 'ccache',
          'asm': 'cc',
          'cc': 'gcc-4.8',
          'cxx': 'g++-4.8'
      },
      'paths': {
          'source': './hhvm_src',
          'install': '/tmp/hhvm-install',
          'package': '/tmp/hhvm-package'
      }
  }

  argp = argparse.ArgumentParser(prog='hhvm_build', description='Build HHVM')

  argp.add_argument('distro', type=str, help='Linux Distribution (deb, etc)')
  argp.add_argument('release', type=str, help='OS Release codename (precise, etc)')
  argp.add_argument('version', type=str, help='HHVM Version')

  argp.add_argument('hhvm_job', type=str, help='HHVM Jenkins workspace')

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

  cfg = Config(args.config, d_cfg)

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

  HHVM_VERSION = args.version
  SOURCE_DIR = os.path.abspath(args.hhvm_job)

  if not os.path.isdir(DISTRO_DIR):
    logging.fatal('Directory {0} doesn\'t exist.'.format(DISTRO_DIR))
    sys.exit(1)

  if SOURCE_DIR is None or not os.path.isdir(SOURCE_DIR):
    SOURCE_DIR = os.path.abspath(cfg.get('paths.source'))

  if not os.path.isdir(SOURCE_DIR):
    log.info('Source code not found. SOURCE_DIR={}'.format(SOURCE_DIR))
    sys.exit(1)

  INSTALL_DIR = os.path.abspath(cfg.get('paths.install'))
  mkdirOrClear(INSTALL_DIR)

  PACKAGE_DIR = os.path.abspath(cfg.get('paths.package'))
  mkdirOrClear(PACKAGE_DIR)

  NIGHTLY = False
  DEVONLY = False
  DEBUG = False

  version_chunks = HHVM_VERSION.split('-')
  new_version_chunks = []
  for i in range(len(version_chunks)):
    chunk = version_chunks[i]
    if i == 1 and chunk == 'nightly':
      NIGHTLY = True
      # new_version_chunks += [chunk]
      continue
    else:
      if chunk == 'dev':
        DEVONLY = True
        DEBUG = True
        continue
      if chunk == 'dbg':
        DEBUG = True
        continue
      new_version_chunks += [chunk]
  HHVM_VERSION = '-'.join(new_version_chunks)
  log.info('HHVM Version {} - Debug: {}, Dev: {}, Nightly: {}'.format(HHVM_VERSION, bool2yn(DEBUG), bool2yn(DEVONLY), bool2yn(NIGHTLY)))

  env_ext = {
      'CC': cfg.get('bin.cc', 'gcc-4.8'),
      'CXX': cfg.get('bin.cxx', 'g++-4.8'),
      'ASM': cfg.get('bin.asm', 'cc'),

      'CMAKE_INCLUDE_PATH': tempfile.NamedTemporaryFile(delete=False).name,
      'CMAKE_LIBRARY_PATH': "/usr/lib/hhvm/",
      'HPHP_HOME': SOURCE_DIR,
      'MYSQL_UNIX_SOCK_ADDR': '/var/run/mysqld/mysqld.sock',
  }

  ENV.merge(env_ext)

  cmake = CMake()
  for k, v in cfg.get('env.cmake.flags', {}).items():
    cmake.setFlag(k, v)

  MAKE_FLAGS = cfg.get('env.make.flags', [])

  cmake.setFlag('CMAKE_BUILD_TYPE', 'Debug' if DEBUG else 'Release')
  cmake.setFlag('CMAKE_INSTALL_PREFIX', '/usr')

  configure_ccache(cfg, cmake)
  configure_distcc(cfg, cmake)
  configure_cotire(cfg, cmake)

  job_flag = '-j' + str(cfg.get('env.make.jobs', 1))
  MAKE_FLAGS += [job_flag]
  NIGHTLY_DATE = datetime.datetime.utcnow().strftime('%Y.%m.%d')
  iteration = int(os.environ.get('BUILD_NUMBER', '1'))
  # NIGHTLY_DATE += '.{:02d}'.format(iteration)

  repo = GitRepository(SOURCE_DIR, None)

  with Chdir(SOURCE_DIR) as sourcedir:
    with log.info('Compile environment:'):
      cmd(['uname', '-a'], echo=False)
      cmd(['lsb_release', '-a'], echo=False)
      cmd(['git', 'log', '-n', '1', '--pretty=oneline'], echo=False)

    hhvm_bin = os.path.join(SOURCE_DIR, 'hphp/hhvm/hhvm')
    rebuild = args.force_rebuild
    if not rebuild and not os.path.isfile(hhvm_bin):
      log.warn('hhvm binaries not found.')
      rebuild = True
    if rebuild and args.package_only:
      log.error('Nothing to package, aborting.')
      sys.exit(1)
    if rebuild:
      with log.info('Preparing to compile...'):

        branch = ''
        if NIGHTLY:
          branch = 'master'
          REG_VERSION = re.compile(r'([0-9.]*-dev)')
          version_file = ''
          with open('hphp/system/idl/constants.idl.json', 'r') as f:
            version_file = f.read()
          with open('hphp/system/idl/constants.idl.json', 'w') as f:
            f.write(REG_VERSION.sub('\1+' + NIGHTLY_DATE, version_file))
          log.info('Version set.')
        else:
          branch = 'HHVM-{}'.format(HHVM_VERSION)

        repo.quiet = False
        repo.CheckForUpdates(remote='origin', branch=branch, quiet=False)
        repo.Pull(remote='origin', branch=branch, cleanup=not args.disable_git_clean)
        repo.UpdateSubmodules()

        distro_info = os.path.join(HHVMBUILD_DIR, 'hhvm', DISTRO_NAME, DISTRO_RELEASE, 'package.yml')
        distro_cfg = Config(distro_info, template_dir='/', variables={
            'SOURCE_DIR': SOURCE_DIR,
            'DISTRO_DIR': os.path.join(HHVMBUILD_DIR, 'hhvm', DISTRO_NAME, DISTRO_RELEASE),
            'HHVMBUILD_DIR': HHVMBUILD_DIR
        })
        if len(distro_cfg.get('prebuild', [])) > 0:
          log.info('Performing prebuild steps...')
          origpath = os.path.abspath(os.getcwd())
          RunCommandsIn(distro_cfg.get('prebuild', []), distro_cfg, {
              'SOURCE_DIR': SOURCE_DIR,
              'DISTRO_DIR': os.path.join(HHVMBUILD_DIR, 'hhvm', DISTRO_NAME, DISTRO_RELEASE),
              'HHVMBUILD_DIR': HHVMBUILD_DIR
          })
          os.chdir(origpath)

      if not cmake.run(cfg.get('bin.cmake', 'cmake')):
        sys.exit(1)

      if not cmd([cfg.get('bin.make', 'make')] + MAKE_FLAGS, critical=True, echo=True):
        sys.exit(1)

    if not os.path.isfile(hhvm_bin):
      log.critical(hhvm_bin + " doesn't exist")
      sys.exit(1)

  if cfg.get('env.packaging.enabled', False):
    stdout, stderr = cmd_output('dpkg-architecture -qDEB_BUILD_GNU_TYPE'.split(), critical=True, echo=False)
    ARCH = (stdout + stderr).strip()

    for build_type in ['main', 'dev']:
      if build_type == 'dev':
        DEVONLY = True
        if DEBUG:
          continue

      pkgname = 'hhvm'
      skeletondirname = 'skeleton'
      suffix = ''
      if DEVONLY:
        pkgname += '-dev'
        suffix = '-dev'
      if NIGHTLY:
        pkgname += '-nightly'
      if DEBUG and not DEVONLY:
        pkgname += '-dbg'

      with log.info('Packaging {}...'.format(pkgname)):

        d_pkg_cfg = {
            'make-workspace': [],
            'fpm': {
                'output-type': 'deb'
            }
        }

        skeletondirname += suffix

        pkginfo_dir = os.path.join(HHVMBUILD_DIR, 'hhvm', DISTRO_NAME, DISTRO_RELEASE + suffix)

        pkg_cfg = Config(os.path.join(pkginfo_dir, 'package.yml'), d_pkg_cfg, template_dir='/', variables={
            'SOURCE_DIR': SOURCE_DIR,
            'DISTRO_DIR': DISTRO_DIR,
            'INSTALL_DIR': INSTALL_DIR,
            'PACKAGE_DIR': PACKAGE_DIR,
            'DISTRO_DIR': DISTRO_DIR,
            'SKELETON_DIR': os.path.join(HHVMBUILD_DIR, 'hhvm', DISTRO_NAME, skeletondirname),
            'HHVMBUILD_DIR': HHVMBUILD_DIR,
            'ARCH': ARCH
        })
        with Chdir(SOURCE_DIR) as sourcedir:
          cmd([cfg.get('bin.make', 'make'), 'install', 'DESTDIR=' + INSTALL_DIR], critical=True)

          if NIGHTLY:
            version = NIGHTLY_DATE + '~' + ('debug' if DEBUG else 'release')
            if DEBUG:
              conflicts = replaces = ['hhvm', 'hhvm-nightly', 'hhvm-dbg']
            else:
              conflicts = replaces = ['hhvm', 'hhvm-dbg']
          else:
            if DEBUG:
              conflicts = replaces = ['hhvm']
            version = HHVM_VERSION + '~' + ('debug' if DEBUG else 'release')

        if len(pkg_cfg.get('make-workspace', [])) > 0:
          log.info('Prepping workspace{} for packaging...'.format(suffix))
          RunCommandsIn(pkg_cfg.get('make-workspace', []), pkg_cfg, {})

        package = ''
        pkgVersion = ''

        with Chdir(PACKAGE_DIR):
          fpm = FPM()
          fpm.input_type = 'dir'
          fpm.output_type = 'deb'
          with log.info('Loading ' + DISTRO_DIR + '/DEBIAN/control...'):
            fpm.LoadControl(DISTRO_DIR + '/DEBIAN/control')

          skeledir = os.path.join(HHVMBUILD_DIR, 'hhvm', DISTRO_NAME, skeletondirname)

          with log.info('Loading {}...'.format(skeledir)):
            fpm.LoadDebianDirectory(os.path.join(skeledir, "DEBIAN"))

          with log.info('Figuring out version number...'):
            while True:
              pkgVersion = version
              if iteration > 0:
                pkgVersion += '-{}'.format(iteration)
              package = os.path.join(HHVMBUILD_DIR, '{name}_{version}.{arch}.deb'.format(name=pkgname, version=pkgVersion, arch=fpm.architecture))
              if not os.path.isfile(package):
                break
              log.warn('%s exists, increasing iterator and trying again.', package)
              iterator += 1
            log.info('package=%s', package)

          if build_type != 'dev':
            with log.info('Determining package dependencies...'):
              shlib_data = os_utils.GetDpkgShlibs([PACKAGE_DIR + '/usr/bin/hhvm'])
              fpm.dependencies = shlib_data['Depends']
              log.info('fpm.dependencies = ' + repr(fpm.dependencies))

          with log.info('Running FPM...'):
            fpm.version = str(version)
            fpm.maintainer = cfg.get('env.packaging.maintainer', 'NOT SET <lol@idk.local>')
            fpm.name = pkgname
            # fpm.provides = ['hhvm'+suffix]
            fpm.conflicts = conflicts
            if build_type != 'dev':
              fpm.configs += ['etc/']
            fpm.iteration = iteration
            fpm.replaces = replaces
            fpm.inputs = ['.']
            fpm.workdir = PACKAGE_DIR
            fpm.Build(str(package))

        if cfg.get('env.packaging.repo-deploy', True):
          with log.info('Adding package to repo...'):
            cmd(['freight-add', package, 'apt/' + DISTRO_RELEASE], critical=True)

          with log.info('Generating repository cache...'):
            cmd(['freight-cache', '-p', '~/.gpass'])

  with log.info('Serializing configuration for extension use...'):
    ext_cfg = {
        'hhvm_version': version,
        'cmake_flags': cmake.flags,
        'make_flags': MAKE_FLAGS,
        'env_ext': env_ext,
    }
    with open('ext.cfg', 'w') as f:
      yaml.dump(ext_cfg, f)

  # extcfg = cfg.get('paths.exts',{})
  # for name, extpath in extcfg:
  #    buildExt(name, extpath)
