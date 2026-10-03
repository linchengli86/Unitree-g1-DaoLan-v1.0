"""Limit numerical-library workers in finite ROS Python helper processes.

Call ``apply_current_process`` before importing ROS/tf/NumPy in a standalone
navigation or pose-observer CLI.  Use ``child_env`` when a long-running server
launches such a helper.  Importing this module has no environment side effects.

Do not apply this budget to the initializer or a parent that launches FastLIO:
its C++/OpenMP children must retain their separately configured worker counts.
The budget does not change TF timestamps, freshness limits or motion limits.
"""

import os


THREAD_BUDGET_VARIABLES = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def child_env(base=None):
    """Return a private helper environment without mutating the parent.

    Override existing larger thread counts: ``setdefault`` would preserve the
    oversubscription this policy is intended to prevent.  Existing ROS paths,
    authentication settings and unrelated variables are copied unchanged.
    """
    environment = dict(os.environ if base is None else base)
    environment.update({name: "1" for name in THREAD_BUDGET_VARIABLES})
    return environment


def apply_current_process():
    """Set this process's helper budget before numerical libraries load.

    This is not a runtime reconfiguration of a library already imported.  A
    fresh helper process is required when NumPy/BLAS has already initialized.
    Return only the fixed budget settings, never the complete environment.
    """
    settings = {name: "1" for name in THREAD_BUDGET_VARIABLES}
    os.environ.update(settings)
    return settings
