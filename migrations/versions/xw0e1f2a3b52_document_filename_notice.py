"""Explain visible document attachments after production feature verification."""

from importlib import import_module

revision = "xw0e1f2a3b52"
down_revision = "xw0e1f2a3b51"
branch_labels = None
depends_on = None

previous = import_module("migrations.versions.xw0e1f2a3b51_document_indexing_notice")
ITEM_ID = previous.ITEM_ID
BODY_EN = (
    "Choose uploaded files from the paperclip menu: their names now appear above the message box, "
    "where you can remove them before sending. After you send, the filenames stay with that message. "
    "For follow-ups with the same files, a compact ‘Files in this chat’ row lets you change the selection. "
    "Removing a file later leaves its name in the original message. "
    "Uploading and indexing show progress, and Send waits until indexing finishes; you can keep writing. "
    "Selections now save on the first try. Attaching a file enables document search and preserves Auto if selected."
)
BODY_RU = (
    "Выберите загруженные файлы в меню со скрепкой: их названия появятся над полем сообщения. "
    "Там же можно убрать лишние файлы перед отправкой. После отправки названия останутся в сообщении, "
    "а над полем ввода появится компактная строка «Файлы в чате». Нажмите на неё, чтобы изменить выбор. "
    "Если убрать файл из чата, его название останется в исходном сообщении. "
    "Во время загрузки и обработки виден индикатор, а отправка недоступна — при этом можно продолжать писать. "
    "Выбор файлов теперь сохраняется с первой попытки. Прикрепление включает поиск по документам, "
    "а выбранный режим «Авто» сохраняется."
)


def upgrade():
    previous._update(BODY_EN, BODY_RU)


def downgrade():
    previous._update(previous.BODY_EN, previous.BODY_RU)
