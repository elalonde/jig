"""The layouts of a project's two clones that every copy of the clone rule must agree on.

The rule says where the other clone of a project is: the product clone demo and the
-dev clone demo-dev sit side by side, and no other place counts. lib/jig_clones.py
holds the rule, and the pre-commit hook and the dispatcher bin/jig each hold a copy,
because neither can import from jig's tree. The function's test, the hook's test,
the dispatcher's test and lint A's test all run the cases below, so that a change to
one copy that the others lack turns a test red.
"""

import collections

PROJECT = "demo"
DEV = f"{PROJECT}-dev"

# The directories of each layout, as paths from the base directory of a fixture.
LAYOUTS = {
    "beside": (DEV, PROJECT),
    # Releases of jig before 0.6 kept the product clone inside its -dev clone, the
    # nested place. A copy left there is never found, so that no tool reads or checks
    # that old code.
    "only nested": (DEV, f"{DEV}/{PROJECT}"),
    "no product clone": (DEV,),
    "no -dev clone": (PROJECT,),
}

# start is the clone the lookup starts from and expected the clone it must find, both
# as paths from the base; finds says which clone is looked for, "product" from a -dev
# clone or "dev" from a product clone. expected None means that there is none.
Case = collections.namedtuple("Case", "name layout start finds expected")

CASES = (
    Case("beside, from the -dev clone", "beside", DEV, "product", PROJECT),
    Case("beside, from the product clone", "beside", PROJECT, "dev", DEV),
    Case("only the nested place exists, from the -dev clone", "only nested", DEV,
         "product", None),
    Case("only the nested place exists, from the product clone", "only nested",
         f"{DEV}/{PROJECT}", "dev", None),
    Case("no product clone, from the -dev clone", "no product clone", DEV, "product", None),
    Case("no -dev clone, from the product clone", "no -dev clone", PROJECT, "dev", None),
)

# For the dispatcher, which starts from the working directory: deep inside the product
# clone, it still finds the project.
DEEP_CASES = (
    Case("deep in the product clone beside", "beside", f"{PROJECT}/lib/deep", "dev", DEV),
)

# The cases that start from a product clone: the only direction that the hook and lint
# A look in, and the one the dispatcher needs when no -dev directory holds the
# working directory.
FROM_PRODUCT = tuple(case for case in CASES if case.finds == "dev")


def build(base, layout):
    """Make the directories of a layout under base and return base."""
    for relative in LAYOUTS[layout]:
        (base / relative).mkdir(parents=True, exist_ok=True)
    return base
