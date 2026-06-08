"""
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

"""

from __future__ import annotations

from typing import List, Optional, Dict, Iterable
from datetime import date
import argparse
import collections
import csv
import io
import json
import os
import re
import subprocess
import sys
from xml.sax.saxutils import escape, quoteattr


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
    id = 1
    fields = [
        "binary:Package",
        "Version",
        "Depends",
        "Provides",
        "Maintainer",
    ]
    _pkgref_re = r"(?P<name>[a-zA-Z0-9\+\-\._]+)\s*(\(\s*(?P<op>(\=|\>\=|\>\>|\<\=|\<\<))\s*(?P<version>[^\)]+)\s*\))?"

    def __init__(self):
        self.id = Package.id
        Package.id += 1
        self.name: Optional[str] = None
        self.version: Optional[str] = None
        self.dependencies: List[PackageDependencyAlt] = []
        self.provides: List[PackageRef] = []
        self.maintainer: Optional[str] = None

    @classmethod
    def from_dict(cls, dict):
        new = Package()
        new.name = dict["binary:Package"]
        new.version = dict["Version"]
        new.dependencies = list(
            map(PackageDependencyAlt, cls.parse_package_refs(dict["Depends"]))
        )
        new.provides = cls.flatten(cls.parse_package_refs(dict["Provides"]))
        new.maintainer = dict["Maintainer"]
        return new

    @classmethod
    def parse_package_ref(cls, raw_ref):
        """Parses `<name> (<op> <version>)"""
        m = re.match(cls._pkgref_re, raw_ref)
        if m:
            return PackageRef(m.group("name"), m.group("op"), m.group("version"))
        else:
            return None

    @classmethod
    def parse_package_ref_alt(cls, raw_ref_alt):
        """Parses <pkgref> | <pkgref> | ..."""
        ref_alt = raw_ref_alt.split("|")
        alts = []
        for raw in ref_alt:
            raw = raw.strip()
            if raw:
                alts.append(cls.parse_package_ref(raw))
        return alts

    @classmethod
    def parse_package_refs(cls, raw_str):
        """Parses `<pkgrefmany>, <pkgrefmany>, ...`"""
        raw_refs = raw_str.split(",")
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


def get_dpkg_data():
    result = subprocess.run(
        [
            "dpkg-query",
            "--show",
            "--showformat",
            ",".join(('"${' + field + '}"' for field in Package.fields)) + "\n",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        print(f"Failed: {result.returncode} {result.stderr}", file=sys.stderr)
        sys.exit(1)

    reader = csv.DictReader(io.StringIO(result.stdout), fieldnames=Package.fields)
    for dict_ in reader:
        yield Package.from_dict(dict_)


def postprocess_packages(packages: Dict[str, Package]):
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


def write_dotfile(fout: io.TextIOWrapper, packages: Iterable[Package]):
    # render the dotfile
    # graphviz library tries to position them which is not useful here
    # so we manually construct
    output = ["digraph Debian {"]

    for package in packages:
        output.append(f'"{package.name}" [label="{package.name}"];')

    for package in packages:
        for alt in package.dependencies:
            if alt.actual is not None:
                output.append(f'"{package.name}" -> "{alt.actual.name}";')

    output.append("}")
    fout.write("\n".join(output))


def write_gexf(fout: io.TextIOWrapper, packages: Iterable[Package]):
    today_iso = date.today().strftime("%Y-%m-%d")
    creator = "debgraph"
    description = "A graph of apt packages on a Debian system."
    nodes = []
    edges = []

    for package in packages:
        nodes.append(
            f"""            <node id="{package.id}" label={quoteattr(package.name)}>
                <attvalues>
                    <attvalue for="0" value={quoteattr(package.maintainer)}/>
                </attvalues>
            </node>"""
        )

    for package in packages:
        for alt in package.dependencies:
            if alt.actual is not None:
                edges.append(f"""            <edge 
                source="{package.id}"
                target="{alt.actual.id}" />""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<gexf xmlns="http://www.gexf.net/1.3" version="1.3" xmlns:viz="http://www.gexf.net/1.3/viz" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.gexf.net/1.3 http://www.gexf.net/1.3/gexf.xsd">
    <meta lastmodifieddate="{today_iso}">
        <creator>{escape(creator)}</creator>
        <description>{escape(description)}</description>
    </meta>
    <graph defaultedgetype="directed" idtype="string" type="static">
        <attributes class="node">
            <attribute id="0" title="maintainer" type="string"/>
        </attributes>
        <nodes count="{len(nodes)}">
{'\n'.join(nodes)}
        </nodes>
        <edges>
{'\n'.join(edges)}
        </edges>
    </graph>
</gexf>"""

    fout.write(xml)


def infer_format(filename: str):
    _, ext = os.path.splitext(filename)
    if ext.lower() == ".dot":
        return "dot"
    elif ext.lower() == ".gexf":
        return "gexf"
    else:
        raise ValueError(
            f"Could not infer format from {filename} with extension {ext.lower()}, please specify it explictly using -t."
        )


def main():
    ap = argparse.ArgumentParser("debgraph")
    ap.add_argument(
        "-o", "--output", default="debian.dot", help="Name of the output file"
    )
    ap.add_argument(
        "-t",
        "--format",
        required=False,
        choices=["dot", "gexf"],
        help="Output format, can be inferred from --output.",
    )
    args = ap.parse_args()

    output_abs_path = os.path.abspath(args.output)
    dirname, filename = os.path.split(output_abs_path)

    format = args.format
    if format is None:
        format = infer_format(filename)
        print(f"Using format {format}", file=sys.stderr)

    os.makedirs(dirname, exist_ok=True)
    packages = {package.name: package for package in get_dpkg_data()}
    postprocess_packages(packages)

    with open(output_abs_path, "w") as fout:
        if format == "dot":
            write_dotfile(fout, packages.values())
        elif format == "gexf":
            write_gexf(fout, packages.values())
        else:
            print(f"Unknown format {format}", file=sys.stderr)
            sys.exit(1)

    print(f"Finished writing output to {output_abs_path}")


def test():
    tests = [
        "apt-transport-https (= 3.2.0)",
        "libappstream5 (= 1)",
    ]
    for test in tests:
        print(parse_package_ref(test))


if __name__ == "__main__":
    main()
