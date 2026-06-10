"""
NeoDB pytest test suite

This package contains all tests converted from Django's unittest framework to pytest.

Test organization:
- test_catalog_*.py: Tests for catalog app modules
- test_users.py: Tests for users app
- test_journal_*.py: Tests for journal app modules
- test_common.py: Tests for common app modules
- test_social.py: Tests for social app modules
- test_takahe.py: Tests for takahe app modules

All tests use pytest fixtures instead of Django's setUp/tearDown methods.
Multi-database tests are marked with @pytest.mark.django_db(databases="__all__").
"""
