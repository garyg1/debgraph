# dpkg-query -W -f='"${binary:Package}","${Version}","${Depends}","${Maintainer}"\n'

import subprocess
import sys
import csv
import io
import re
import json

class GenericEncoder(json.JSONEncoder):
    def default(self, obj):
        return obj.__dict__

class PackageRef:
    def __init__(self, name, op=None, version=None):
        self.name = name
        self.op = op
        self.version = version

    def __repr__(self):
        return json.dumps(self.__dict__, cls=GenericEncoder)


class Package:
    fields = [
        'binary:Package',
        'Version',
        'Depends',
        'Provides',
        'Maintainer',
    ]
    _pkgref_re = r"(?P<name>[a-zA-Z0-9\+\-\._]+)\s*(\(\s*(?P<op>(\=|\>\=|\>\>|\<\=|\<\<))\s*(?P<version>[^\)]+)\s*\))?"

    def __init__(self):
        pass

    @classmethod
    def from_dict(cls, dict):
        new = Package()
        new.package = dict['binary:Package']
        new.version= dict['Version']
        new.dependencies = cls.parse_package_refs(dict['Depends'])
        new.provides = cls.parse_package_refs(dict['Provides'])
        new.maintainer = dict['Maintainer']
        return new

    
    @classmethod
    def parse_package_ref(cls, raw_ref):
        """Parses `<name> (<op> <version>)"""
        m = re.match(cls._pkgref_re, raw_ref)
        if m:
            return PackageRef(m.group('name'), m.group('op'), m.group('version'))
        else:
            return None

    @classmethod
    def parse_package_ref_alt(cls, raw_ref_alt):
        """Parses <pkgref> | <pkgref> | ..."""
        ref_alt = raw_ref_alt.split('|')
        alts = []
        for raw in ref_alt:
            raw = raw.strip()
            if raw:
                alts.append(cls.parse_package_ref(raw))
        return alts
        
    @classmethod
    def parse_package_refs(cls, raw_str):
        """Parses `<pkgrefmany>, <pkgrefmany>, ...`"""
        raw_refs = raw_str.split(',')
        refs = []
        for raw_ref in raw_refs:
            raw_ref = raw_ref.strip()
            if raw_ref:
                ref = cls.parse_package_ref_alt(raw_ref)
                refs.append(ref)
        return refs

    def __repr__(self):
        return json.dumps(self.__dict__, cls=GenericEncoder)

def main():
    result = subprocess.run(
        ['dpkg-query', '--show', '--showformat', ','.join(('"${' + field + '}"' for field in Package.fields )) + '\n'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        print(f"Failed: {result.returncode} {result.stderr}")
        sys.exit(1)
    
    reader = csv.DictReader(io.StringIO(result.stdout), fieldnames=Package.fields)

    for pkg in list(reader)[:10]:
        package = Package.from_dict(pkg)
        print(package)
    

    # for each dependency find which actually provides it

    # render the dotfile

def test():
    tests = [
        'apt-transport-https (= 3.2.0)',
        'libappstream5 (= 1)',
    ]
    for test in tests:
        print(parse_package_ref(test))


if __name__ == '__main__':
    main()