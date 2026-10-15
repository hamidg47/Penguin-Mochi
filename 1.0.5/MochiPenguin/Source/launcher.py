"""Frozen entry point. Keep personal state out of the application bundle."""
import os
import sys
from pathlib import Path

smoke = '--smoke-test' in sys.argv
if smoke:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    if not os.getenv('MOCHI_DATA_DIR'):
        raise RuntimeError('Smoke testing requires an isolated MOCHI_DATA_DIR')

import desktop_pet_v7_goose_style_penguin as pet
from PySide6.QtCore import QLockFile, QTimer

lock = QLockFile(str(pet.APP_DATA / 'app.lock'))
lock.setStaleLockTime(0)
if not lock.tryLock(100):
    sys.exit(0)
if smoke:
    pet.IS_WINDOWS = False
program = pet.CompanionAppV7()
program.app.setApplicationName(pet.APP_NAME)
program.app.setApplicationVersion(pet.APP_VERSION)
if smoke:
    program.pet.sounds_enabled = False
    errors = []
    def report_error(kind, value, traceback):
        errors.append(str(value))
        (pet.APP_DATA / 'smoke-errors.txt').write_text('\n'.join(errors), encoding='utf-8')
    sys.excepthook = report_error
    def finish():
        program.pet.save_state()
        if not errors and pet.SAVE_FILE.exists():
            (pet.APP_DATA / 'smoke-passed.txt').write_text('Startup, render, save and shutdown passed.', encoding='utf-8')
        program.quit()
    QTimer.singleShot(1200, finish)
else:
    QTimer.singleShot(1500, lambda: program.pet.say('Right-click me for food, games and settings!', 5))
try:
    sys.exit(program.run())
finally:
    lock.unlock()
