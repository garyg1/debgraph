import argparse
import os
import pathlib
import sys
from debgraph import DpkgReader, GraphFileWriter


def get_fixtures(name):
    dirname = pathlib.Path(__file__).parent

    def get_fixture_path(*suffix: str):
        return os.path.join(dirname, "fixtures", os.path.extsep.join([name, *suffix]))

    in_fixture = get_fixture_path("in")

    out_fixtures = [
        [get_fixture_path("jsonl"), GraphFileWriter._write_jsonl],
        [get_fixture_path("gexf"), GraphFileWriter._write_gexf],
        [get_fixture_path("dot"), GraphFileWriter._write_dotfile],
    ]

    return in_fixture, out_fixtures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", type=str, help="The name of this fixture")
    ap.add_argument("-f", "--force", action="store_true", help="Overwrite")
    ap.add_argument(
        "-l", "--long", action="store_true", help="Whether to include long fields"
    )
    args = ap.parse_args()

    in_fixture, out_fixtures = get_fixtures(args.name)

    if not args.force:
        for fixture_path in [in_fixture, *(fixture for fixture, _ in out_fixtures)]:
            if os.path.exists(fixture_path):
                print(
                    f"Found existing fixture at {fixture_path}, use -f to overwrite.",
                    file=sys.stderr,
                )
                sys.exit(1)

    s = DpkgReader._get_dpkg_stdout()
    with open(in_fixture, "w") as in_file:
        in_file.write(s)

    packages = DpkgReader._parse_dpkg_stdout(s, args.long)

    for fixture_path, fn in out_fixtures:
        with open(fixture_path, "w") as out_file:
            fn(out_file, packages)


if __name__ == "__main__":
    main()
