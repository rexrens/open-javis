"""Cordis plugins mounted by ``cordis.yml``.

Every harness capability is one module here: it exports ``apply`` (plus
optional ``inject`` and ``Config``) and contributes a service, a tool or a
front end to the shared context. Removing a row from the composition removes
the capability.
"""
