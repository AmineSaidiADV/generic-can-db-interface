from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import cantools
from cantools.database.can.database import Database


@dataclass
class MessageInfo:
    frame_id: int
    name: str
    length: int
    senders: List[str]
    receivers: List[str]
    signals: List[str]


class KCDDatabase:
    def __init__(self, db: Database, source_path: Path) -> None:
        self._db = db
        self._source_path = Path(source_path)

    @property
    def db(self) -> Database:
        return self._db

    @property
    def source_path(self) -> Path:
        return self._source_path

    @property
    def nodes(self) -> List[str]:
        return [n.name for n in self._db.nodes]

    @property
    def messages(self) -> List[MessageInfo]:
        msgs: List[MessageInfo] = []
        for m in self._db.messages:
            msgs.append(
                MessageInfo(
                    frame_id=m.frame_id,
                    name=m.name,
                    length=m.length,
                    senders=list(m.senders or []),
                    receivers=list(m.receivers or []),
                    signals=[s.name for s in m.signals],
                )
            )
        return msgs

    def producer_messages_for(self, node: str) -> List[MessageInfo]:
        return [m for m in self.messages if node in m.senders]

    def consumer_messages_for(self, node: str) -> List[MessageInfo]:
        return [m for m in self.messages if node not in m.senders]

    def get_message(self, name: str):
        return self._db.get_message_by_name(name)


def load_kcd(path: str | Path) -> KCDDatabase:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"KCD file not found: {p}")
    db = cantools.database.load_file(str(p))
    return KCDDatabase(db=db, source_path=p)
