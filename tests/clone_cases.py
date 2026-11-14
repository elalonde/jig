"""The layouts of a project's two clones that every copy of the clone rule must agree on.

The rule says where the other clone of a project is: the product clone demo and the
-dev clone demo-dev sit side by side, and the place of the releases before 0.6, a
product clone inside its -dev clone at demo-dev/demo, counts only when nothing sits
beside it. lib/jig_clones.py holds the rule, and the pre-commit hook and the
dispatcher bin/jig each hold a copy, because neither can import from jig's tree.
The function's test, the hook's test and the dispatcher's test all run the cases
below, so that a change to one copy that the others lack turns a test red.
"""

import collections

PROJECT = "demo"
DEV = f"{PROJECT}-dev"

# The directories of each layout, as paths from the base directory of a fixture.
LAYOUTS = {
    "beside": (DEV, PROJECT),
    "inside": (DEV, f"{DEV}/{PROJECT}"),
    "both": (DEV, PROJECT, f"{DEV}/{PROJECT}"),
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
    Case("inside, from the -dev clone", "inside", DEV, "product", f"{DEV}/{PROJECT}"),
    Case("inside, from the product clone", "inside", f"{DEV}/{PROJECT}", "dev", DEV),
    # The clone beside wins over a leftover one inside.
    Case("both, from the -dev clone", "both", DEV, "product", PROJECT),
    # From either product clone, the -dev clone is the same one.
    Case("both, from the product clone beside", "both", PROJECT, "dev", DEV),
    Case("both, from the product clone inside", "both", f"{DEV}/{PROJECT}", "dev", DEV),
    Case("no product clone, from the -dev clone", "no product clone", DEV, "product", None),
    Case("no -dev clone, from the product clone", "no -dev clone", PROJECT, "dev", None),
)

# For the dispatcher, which starts from the working directory: deep inside a product
# clone, in either place, it still finds the project.
DEEP_CASES = (
    Case("deep in the product clone beside", "beside", f"{PROJECT}/lib/deep", "dev", DEV),
    Case("deep in the product clone inside", "inside", f"{DEV}/{PROJECT}/lib/deep", "dev",
         DEV),
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
