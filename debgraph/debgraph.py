'''
    Debgraph, a program like debtree to view ALL the Debian packages on your system.
    Copyright (C) 2026  Gary Gurlaskie

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License, version 3 as
    published by the Free Software Foundation.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.

'''

from __future__ import annotations

import collections
import subprocess
import sys
import csv
import io
import re
import json
from typing import List, Optional, Dict

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


class PackageDependencyAlt:
    def __init__(self, alts: List[PackageRef]):
        self.alts = alts
        self.actual: Optional[Package] = None

    
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
        self.name: Optional[str] = None
        self.version: Optional[str] = None
        self.dependencies: List[PackageDependencyAlt] = []
        self.provides: List[PackageRef] = []
        self.maintainer: Optional[str] = None 

    @classmethod
    def from_dict(cls, dict):
        new = Package()
        new.name = dict['binary:Package']
        new.version= dict['Version']
        new.dependencies = list(map(PackageDependencyAlt, cls.parse_package_refs(dict['Depends'])))
        new.provides = cls.flatten(cls.parse_package_refs(dict['Provides']))
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

    @staticmethod
    def flatten(l):
        return [x for sublist in l for x in sublist]

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

    packages: Dict[str, Package] = {}
    for dict_ in reader:
        package = Package.from_dict(dict_)
        assert package.name not in packages
        packages[package.name] = package
    
    providers = collections.defaultdict(list)
    for package in packages.values():
        for provided in package.provides:
            providers[provided.name].append(package)
        
        
    # for each dependency find which actually provides it
    for package in packages.values():
        for alt in package.dependencies:
            for requested in alt.alts:
                if requested.name in packages:
                    alt.actual = packages[requested.name]
                    break
                if requested.name in providers:
                    alt.actual = providers[requested.name][0]
                    break

    # render the dotfile
    # graphviz library tries to position them which is not useful
    # so write to stdout
    output = [
        "digraph Debian {"
    ]

    for package in packages.values():
        output.append(f'"{package.name}" [label="{package.name}"];')
    
    for package in packages.values():
        for alt in package.dependencies:
            if alt.actual is not None:
                output.append(f'"{package.name}" -> "{alt.actual.name}";')

    output.append("}")

    with open('debian.dot', 'w') as fout:
        fout.write('\n'.join(output))



def test():
    tests = [
        'apt-transport-https (= 3.2.0)',
        'libappstream5 (= 1)',
    ]
    for test in tests:
        print(parse_package_ref(test))


if __name__ == '__main__':
    main()