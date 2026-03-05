import tempfile
import unittest
from pathlib import Path

from tools.backup_db import backup_database
from tools.restore_db import restore_database


class DbBackupRestoreTests(unittest.TestCase):
    def test_backup_and_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'iom.db'
            backup_dir = root / 'backups'
            restore_target = root / 'restored.db'

            source.write_text('sample-db-content', encoding='utf-8')

            backup_path = backup_database(source, backup_dir)
            self.assertTrue(backup_path.exists())

            restored_path = restore_database(backup_path, restore_target, force=False)
            self.assertEqual(restored_path, restore_target)
            self.assertTrue(restore_target.exists())
            self.assertEqual(restore_target.read_text(encoding='utf-8'), 'sample-db-content')

    def test_restore_requires_force_when_target_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            backup = root / 'backup.db'
            target = root / 'iom.db'
            backup.write_text('backup-content', encoding='utf-8')
            target.write_text('old-content', encoding='utf-8')

            with self.assertRaises(FileExistsError):
                restore_database(backup, target, force=False)

            restore_database(backup, target, force=True)
            self.assertEqual(target.read_text(encoding='utf-8'), 'backup-content')


if __name__ == '__main__':
    unittest.main()
