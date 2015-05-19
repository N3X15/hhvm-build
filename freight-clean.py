import re
import os
import sys
import yaml

NUM_TO_KEEP = 5
PACKAGE_DEFS = {}
FOUND_PACKAGES = {}

if __name__ == '__main__':
    with open('freight-clean.yml', 'r') as f:
        yml = yaml.load(f)
        NUM_TO_KEEP = yml.get('settings', {}).get('num-to-keep', 5)
        PACKAGE_DEFS = {}
        for name, pkgSettings in yml['packages'].items():
            PACKAGE_DEFS[name] = pkgSettings
            '(?P<pkgID>[a-z0-9\-]+)_(?P<version>[0-9a-z\.]+)~release-(?P<sortkey>[0-9]+)\.amd64\.deb'
            PACKAGE_DEFS[name]['regex'] = re.compile(pkgSettings['regex'])

    for root, _, files in os.walk('/var/lib/freight/apt/jessie'):
        for file in files:
            fullpath = os.path.join(root, file)
            filename = os.path.basename(fullpath)
            _, ext = os.path.splitext(fullpath)

            if ext not in ('.deb'):
                continue
            for pkgID, packagecfg in PACKAGE_DEFS.items():
                m = packagecfg['regex'].match(filename)
                if m:
                    sortkey = m.group('sortkey')
                    fPkgID = m.group('pkgID')
                    if fPkgID is None:
                        fPkgID = pkgID
                    if fPkgID not in FOUND_PACKAGES:
                        FOUND_PACKAGES[fPkgID] = {}
                    FOUND_PACKAGES[fPkgID][sortkey] = fullpath
                    break

    for pkgID, archDict in FOUND_PACKAGES.items():
        pkgsLeft = NUM_TO_KEEP
        print('Scanning for outdated {} packages...'.format(pkgID))
        for sortKey in reversed(sorted(list(archDict.keys()))):
            fullpath = archDict[sortKey]
            if pkgsLeft <= 0:
                if os.path.isfile(fullpath):
                    print('  RM {}'.format(fullpath))
                    os.remove(fullpath)
            #else:
            #    print('  KEEP {}'.format(fullpath))
            pkgsLeft -= 1
