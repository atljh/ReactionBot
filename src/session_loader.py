from pathlib import Path
from typing import Generator, Tuple, Dict, Any, Optional, List
from .utils import json_read


class SessionLoader:
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = Path(sessions_dir)
        if not self.sessions_dir.exists():
            self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def find_sessions(self) -> Generator[Tuple[Path, Path, Dict[str, Any]], None, None]:
        for item in self.sessions_dir.glob("*.session"):
            json_file = item.with_suffix(".json")
            if not json_file.is_file():
                continue
            json_data = json_read(json_file)
            if not json_data:
                continue
            yield item, json_file, json_data

    def find_tdata_folders(self) -> Generator[Tuple[Path, str], None, None]:
        for item in self.sessions_dir.iterdir():
            if not item.is_dir():
                continue

            tdata_path = item / "tdata"
            if not tdata_path.exists():
                tdata_path = item
                key_files = ["key_data", "key_datas"]
                has_key = any((tdata_path / kf).exists() for kf in key_files)
                if not has_key:
                    continue

            phone = item.name
            yield tdata_path, phone

    def get_all_sessions(self) -> List[Dict[str, Any]]:
        sessions = []
        for session_file, json_file, json_data in self.find_sessions():
            sessions.append({
                "session_file": str(session_file),
                "json_file": str(json_file),
                "phone": json_data.get("phone", session_file.stem),
                "json_data": json_data
            })
        return sessions

    def get_session(self, phone: str) -> Optional[Tuple[Path, Path, Dict[str, Any]]]:
        phone_clean = phone.replace("+", "").strip()

        for session_file, json_file, json_data in self.find_sessions():
            session_phone = json_data.get("phone", session_file.stem)
            session_phone_clean = str(session_phone).replace("+", "").strip()

            if session_phone_clean == phone_clean or session_file.stem == phone_clean:
                return session_file, json_file, json_data

        return None

    def count_sessions(self) -> int:
        return sum(1 for _ in self.find_sessions())

    def count_tdata(self) -> int:
        return sum(1 for _ in self.find_tdata_folders())
