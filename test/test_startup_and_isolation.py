"""Regression tests for application startup and test-database isolation."""
import hashlib
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

from app import app as flask_app  # noqa: E402
from core.database import db  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON_FILES = ['app.py', 'blockchain.py', 'core/__init__.py',
                'core/crypto.py', 'core/database.py']


def _env_without_db_url():
    env = os.environ.copy()
    env.pop('DATABASE_URL', None)
    return env


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run_python(code, cwd, env, timeout=120):
    return subprocess.run([sys.executable, '-c', code], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=timeout)


def _copy_project(tmp):
    sandbox = Path(tmp) / 'project'
    shutil.copytree(
        PROJECT_ROOT, sandbox,
        ignore=shutil.ignore_patterns('instance', '__pycache__', '.pytest_cache',
                                      '.git', 'venv', '.venv', '*.db'),
    )
    return sandbox


class TestApplicationStartup(TestCase):

    def test_source_files_compile(self):
        for rel in PYTHON_FILES:
            with self.subTest(file=rel):
                py_compile.compile(str(PROJECT_ROOT / rel), doraise=True)

    def test_app_imports_and_seeds_genesis_in_fresh_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / 'startup.db'
            env = _env_without_db_url()
            env['DATABASE_URL'] = f'sqlite:///{db_path}'
            result = _run_python(
                'from app import app\n'
                'c = app.test_client()\n'
                'assert c.get("/").status_code == 200\n'
                'assert c.get("/api/health").get_json()["status"] == "ok"\n'
                'assert c.get("/chain").get_json()["length"] == 1\n',
                PROJECT_ROOT, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM block').fetchone()[0], 1)

    def test_default_database_uri_unchanged(self):
        # Importing app creates the default DB, so do it in a throwaway copy.
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = _copy_project(tmp)
            result = _run_python(
                'from app import app\n'
                'print(app.config["SQLALCHEMY_DATABASE_URI"])\n',
                sandbox, _env_without_db_url())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'sqlite:///blockchain.db')

    def test_core_routes_registered(self):
        rules = {r.rule for r in flask_app.url_map.iter_rules()}
        for route in ['/', '/api/health', '/api/stats', '/chain', '/mine',
                      '/transactions/new', '/wallet/transactions',
                      '/wallet/create', '/api/sign', '/api/balance']:
            self.assertIn(route, rules)


class TestDatabaseIsolation(TestCase):

    def test_test_engine_is_in_memory(self):
        with flask_app.app_context():
            self.assertIn(db.engine.url.database, (None, '', ':memory:'))

    def test_suite_leaves_application_database_untouched(self):
        """Run test_blockchain.py in a sandboxed copy that has a seeded
        application DB and check the DB is byte-for-byte unchanged.
        DATABASE_URL is removed from the child env so isolation must come
        from the test module itself."""
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = _copy_project(tmp)
            for extra in sandbox.joinpath('test').glob('test_*.py'):
                if extra.name != 'test_blockchain.py':
                    extra.unlink()

            env = _env_without_db_url()
            seed = _run_python(
                'from app import app\n'
                'c = app.test_client()\n'
                'assert c.get("/mine").status_code == 200\n'
                'assert c.get("/mine").status_code == 200\n',
                sandbox, env)
            self.assertEqual(seed.returncode, 0, seed.stderr)

            app_db = sandbox / 'instance' / 'blockchain.db'
            self.assertTrue(app_db.exists(), 'seeding did not create the application DB')
            digest_before = _sha256(app_db)
            with sqlite3.connect(app_db) as conn:
                blocks_before = conn.execute('SELECT COUNT(*) FROM block').fetchone()[0]
            self.assertEqual(blocks_before, 3)

            run = subprocess.run(
                [sys.executable, '-m', 'unittest', 'test.test_blockchain'],
                cwd=sandbox, env=env, capture_output=True, text=True, timeout=300,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

            self.assertEqual(_sha256(app_db), digest_before)
            with sqlite3.connect(app_db) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM block').fetchone()[0],
                                 blocks_before)
