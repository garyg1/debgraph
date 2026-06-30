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

from typing import List, Optional, Dict, Iterable, Any
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
import graphviz

__version__ = "0.2.0"

class GenericEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, PackageDependencyAlternatives):
            return {
                **obj.__dict__,
                "actuals": [{ "name": actual.name, "version": actual.version } for actual in obj.actuals],
            }
        return obj.__dict__

class DebgraphError(Exception):
    def __init__(self, message, posix_status):
        self.posix_status = posix_status
        self.message = message
        super().__init__(message)

class PackageDependency:
    """
    Represents a dpkg package dependency, such as "apt-transport-https (= 3.2.0)".

    See: https://www.debian.org/doc/debian-policy/ch-relationships.html.
    """
    def __init__(self, name, op=None, version=None):
        self.name = name
        self.op = op
        self.version = version

    def __repr__(self):
        return json.dumps(self.__dict__, cls=GenericEncoder)


class PackageDependencyAlternatives:
    """
    Represents a dpkg package alternative dependency list, such as "apt-transport-https (= 3.2.0) | libappstream5 (= 1)".

    See: https://www.debian.org/doc/debian-policy/ch-relationships.html.
    """
    def __init__(self, alts: List[PackageDependency]):
        self.alts = alts
        self.actuals: List[Package] = []

    def register_actual(self, actual: Package):
        # use list scan to simplify serialization
        if actual not in self.actuals:
            self.actuals.append(actual)

    def __repr__(self):
        return json.dumps(self.__dict__, cls=GenericEncoder)


def _identity_mapper(s: str) -> str:
    return s


def _alt_syntax_mapper(s: str) -> str:
    if s is None:
        return None
    serialized = Package._parse_package_refs(s)
    return str(serialized)


class Package:
    """
    Represents a dpkg binary package. The supported fields are listed in get_all_dpkg_fields.
    Implementation is not thread-safe.

    See: https://www.debian.org/doc/debian-policy/ch-binary.html
    """
    _id = 1
    _fields = [
        "binary:Package",
        "Depends",
    ]
    _extra_field_mapping = [
        ("binary:Synopsis", "binary:Synopsis", _identity_mapper, "string"),
        ("Breaks", "Breaks", _alt_syntax_mapper, "string"),
        ("Description", "Description", _identity_mapper, "string"),
        ("Enhances", "Enhances", _alt_syntax_mapper, "string"),
        ("Installed-Size", "InstalledSizeKB", _identity_mapper, "integer"),
        ("Essential", "IsEssential", _identity_mapper, "string"),
        ("Maintainer", "Maintainer", _identity_mapper, "string"),
        ("Origin", "Origin", _identity_mapper, "string"),
        ("Provides", "Provides", _alt_syntax_mapper, "string"),
        ("Recommends", "Recommends", _alt_syntax_mapper, "string"),
        ("Replaces", "Replaces", _alt_syntax_mapper, "string"),
        ("Section", "Section", _identity_mapper, "string"),
        ("source:Package", "source:Package", _identity_mapper, "string"),
        ("source:Version", "source:Version", _identity_mapper, "string"),
        ("Suggests", "Suggests", _alt_syntax_mapper, "string"),
        ("Version", "Version", _identity_mapper, "string"),
    ]
    _pkgref_re = r"(?P<name>[a-zA-Z0-9\+\-\._]+)\s*(\(\s*(?P<op>(\=|\>\=|\>\>|\<\=|\<\<))\s*(?P<version>[^\)]+)\s*\))?"

    def __init__(self):
        self.id = Package._id
        Package._id += 1

        self.name: Optional[str] = None
        self.version: Optional[str] = None
        self.dependencies: List[PackageDependencyAlternatives] = []
        self.provides: List[PackageDependency] = []
        self.extra: collections.defaultdict[str, Optional[str]] = (
            collections.defaultdict(None)
        )

    @classmethod
    def get_all_dpkg_fields(cls):
        fields = [
            *cls._fields,
            *(dpkg_name for dpkg_name, _, _, _ in cls._extra_field_mapping),
        ]
        return fields

    @classmethod
    def get_all_extra_output_fields(cls):
        return [(output_name, output_type) for _, output_name, _, output_type in cls._extra_field_mapping]


    def __repr__(self):
        return json.dumps(self.__dict__, cls=GenericEncoder)

    def get_no_dep_repr(self):
        return {
            "name": self.name,
            "version": self.version
        }

    @classmethod
    def _from_dict(cls, dict: Dict[str, str]):
        new = cls()
        new.name = dict["binary:Package"]
        new.version = dict["Version"]
        new.dependencies = [
            PackageDependencyAlternatives(altlist)
            for altlist in cls._parse_package_refs(dict["Depends"])
        ]
        new.provides = cls._flatten(cls._parse_package_refs(dict["Provides"]))

        for dpkg_field, output_field, map_fn, _ in cls._extra_field_mapping:
            new.extra[output_field] = map_fn(dict.get(dpkg_field))
        new.extra["Version"] = new.version

        return new

    @classmethod
    def _parse_package_ref(cls, raw_ref):
        """Parses a pkgref, i.e.,  `<name> (<op> <version>)"""
        m = re.match(cls._pkgref_re, raw_ref)
        if m:
            return PackageDependency(m.group("name"), m.group("op"), m.group("version"))
        else:
            return None

    @classmethod
    def _parse_package_ref_alt(cls, raw_ref_alt):
        """Parses a pkgrefalt, i.e., `<pkgref> | <pkgref> | ...`"""
        ref_alt = raw_ref_alt.split("|")
        alts = []
        for raw in ref_alt:
            raw = raw.strip()
            if raw:
                alts.append(cls._parse_package_ref(raw))
        return alts

    @classmethod
    def _parse_package_refs(cls, raw_str):
        """Parses a list of pkgrefalt, i.e., `<pkgrefalt>, <pkgrefalt>, ...`"""
        raw_refs = raw_str.split(",")
        refs = []
        for raw_ref in raw_refs:
            raw_ref = raw_ref.strip()
            if raw_ref:
                ref = cls._parse_package_ref_alt(raw_ref)
                refs.append(ref)
        return refs

    @staticmethod
    def _flatten(l):
        return [x for sublist in l for x in sublist]

class DpkgReader:
    start_entry_delimiter = "[[debgraph magic start entry]]\n"
    comma_delimiter = "[[debgraph magic comma]]\n"

    @classmethod
    def _get_dpkg_stdout(cls):
        # dpkg-query doesn't have a way of escaping CSV, so we use magic strings
        # in order to produce machine-readable output.

        cmd = [
            "dpkg-query",
            "--show",
            "--showformat",
            cls.start_entry_delimiter
            + cls.comma_delimiter.join(
                ("${" + field + "}" for field in Package.get_all_dpkg_fields())
            ),
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            print(f"Failed: {result.returncode} {result.stderr}", file=sys.stderr)
            

        return result.stdout
    
    @classmethod
    def _parse_dpkg_stdout(cls, s: str) -> List[Package]:
        i = 0
        packages = {}
        while i < len(s):
            next_idx = s.find(cls.start_entry_delimiter, i)
            if next_idx == -1:
                next_idx = len(s)
            package_entry = s[i:next_idx]
            values = package_entry.split(cls.comma_delimiter)
            if len(values) == len(Package.get_all_dpkg_fields()):
                dict_ = {
                    field: val for field, val in zip(Package.get_all_dpkg_fields(), values)
                }
                package = Package._from_dict(dict_)
                if package.name in packages:
                    raise DebgraphError(f"Found duplicate packages: {package}, {packages[package.name]}", 1)
                packages[package.name] = package

            i = next_idx + len(cls.start_entry_delimiter)
        
        cls._postprocess_packages(packages)
        return list(packages.values())

    @classmethod
    def _postprocess_packages(cls, packages: Dict[str, Package]):
        providers = collections.defaultdict(list)
        for package in packages.values():
            for provided in package.provides:
                providers[provided.name].append(package)

        # for each dependency find the one(s) that actually provide it
        for package in packages.values():
            for alt in package.dependencies:
                for requested in alt.alts:
                    if requested.name in packages:
                        alt.register_actual(packages[requested.name])
                    if requested.name in providers:
                        alt.register_actual(providers[requested.name][0])


class GraphFileWriter:
    @staticmethod
    def _write_jsonl(fout: io.TextIOWrapper, packages: Iterable[Package]):
        fout.write("\n".join(map(str, packages)))

    @staticmethod
    def _write_dotfile(fout: io.TextIOWrapper, packages: Iterable[Package]):
        dot = graphviz.Digraph("Debian")

        for package in packages:
            dot.node(package.name, label=package.name, _attributes=package.extra)

        for package in packages:
            for alt in package.dependencies:
                for actual in alt.actuals:
                    dot.edge(package.name, actual.name, label=str(alt.alts))

        fout.write(dot.source)

    @staticmethod
    def _write_gexf(fout: io.TextIOWrapper, packages: Iterable[Package]):
        today_iso = date.today().strftime("%Y-%m-%d")
        creator = "debgraph"
        description = "A graph of apt packages on a Debian system."
        nodes = []
        edges = []
        node_attributes = []
        edge_attributes = []

        field_to_index = {
            field: idx for idx, (field, _) in enumerate(Package.get_all_extra_output_fields())
        }
        for idx, (field, type_) in enumerate(Package.get_all_extra_output_fields()):
            node_attributes.append(
                f"""            <attribute id="{idx}" title="{field}" type="{type_}"/>"""
            )

        edge_attributes.append(
            f"""            <attribute id="0" title="alts" type="string"/>"""
        )

        for package in packages:
            attvalues = []
            for field, value in package.extra.items():
                attvalues.append(
                    f"""                    <attvalue for="{field_to_index[field]}" value={quoteattr(value)} />"""
                )
            nodes.append(
                f"""            <node id="{package.id}" label={quoteattr(package.name)}>
                <attvalues>
{'\n'.join(attvalues)}
                </attvalues>
                </node>"""
            )

        for package in packages:
            for alt in package.dependencies:
                for actual in alt.actuals:
                    attvalues = []
                    attvalues.append(
                        f"""                    <attvalue for="0" value={quoteattr(str(alt.alts))} />"""
                    )
                    edges.append(
                        f"""            <edge source="{package.id}" target="{actual.id}">
                <attvalues>
{'\n'.join(attvalues)}
                </attvalues>
                </edge>"""
                    )

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<gexf xmlns="http://www.gexf.net/1.3" version="1.3" xmlns:viz="http://www.gexf.net/1.3/viz" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.gexf.net/1.3 http://www.gexf.net/1.3/gexf.xsd">
    <meta lastmodifieddate="{today_iso}">
        <creator>{escape(creator)}</creator>
        <description>{escape(description)}</description>
    </meta>

    <graph defaultedgetype="directed" idtype="string" type="static">
        <attributes class="node">
{'\n'.join(node_attributes)}
        </attributes>

        <attributes class="edge">
{'\n'.join(edge_attributes)}
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

_supported_formats = {
    "dot": GraphFileWriter._write_dotfile,
    "gexf": GraphFileWriter._write_gexf,
    "jsonl": GraphFileWriter._write_jsonl,
}

def _infer_format(filename: str):
    _, ext = os.path.splitext(filename)
    format_str = ext[len(os.path.extsep):].lower()
    if format_str in _supported_formats:
        return format_str
    else:
        raise DebgraphError(
            f"Could not infer format from {filename} with extension {ext.lower()}, please specify it explictly using -t.", 1
        )


def run_debgraph(argv = None, override_input_stream: Optional[str] = None):
    ap = argparse.ArgumentParser("debgraph")
    ap.add_argument(
        "output", nargs="?", default="debian.dot", help="Name of the output file"
    )
    ap.add_argument(
        "-t",
        "--format",
        required=False,
        choices=_supported_formats.keys(),
        help="Output format, can be inferred from --output.",
    )
    ap.add_argument(
        "-v", "--version", help="Print version and exit.", action='store_true',
    )
    args = ap.parse_args(argv)

    if args.version:
        raise DebgraphError(f"Debgraph {__version__}", 0)
    
    # reset numbering for this graph
    Package._id = 1

    output_abs_path = os.path.abspath(args.output)
    dirname, filename = os.path.split(output_abs_path)

    format = args.format
    if format is None:
        format = _infer_format(filename)
        print(f"Using format {format}", file=sys.stderr)
    write_fn = _supported_formats[format]

    s = DpkgReader._get_dpkg_stdout() if not override_input_stream else override_input_stream
    packages = DpkgReader._parse_dpkg_stdout(s)

    os.makedirs(dirname, exist_ok=True)
    with open(output_abs_path, "w") as fout:
        write_fn(fout, packages)

    print(f"Finished writing output to {output_abs_path}")

def main():
    try:
        run_debgraph()
    except DebgraphError as e:
        print(e.message, file=sys.stdout if e.posix_status == 0 else sys.stderr)
        sys.exit(e.posix_status)

if __name__ == "__main__":
    main()
