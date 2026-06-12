import os
import sysconfig

# SQLAlchemy's compiled cyextension modules don't declare free-threading
# support, so importing them silently re-enables the GIL. On a free-threaded
# build force the pure-Python fallbacks instead. Must run before anything
# imports sqlalchemy (db.py via sqlmodel), hence the package __init__.
# (tests/conftest.py sets the same variable because test modules may import
# sqlalchemy before this package.)
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")
