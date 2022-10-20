import subprocess
from enum import Enum, auto

import toml
from core import values

default_version_dict = {"major": 0, "minor": 1}


class VersionType(Enum):
    MAJOR = 'major'
    MINOR = 'minor'
    PATCH = auto()
    LAST_UPDATE = 'last_update'


class Version():
    def __init__(self):
        self._set_properties(toml.load(values.VERSION_CONFIG_PATH))

    def get(self, version_type: VersionType | None = None) -> str:
        if version_type:
            return self.version_dict[version_type.value]
        else:
            return f'{self.major}.{self.minor}.{self.patch}'

    def set(self, version_type: VersionType, version_value: int) -> None:
        self.version_dict[version_type.value] = version_value
        if version_type is VersionType.MAJOR:
            self.version_dict[VersionType.MINOR.value] = 0
        last_update = Version._get_last_commit_sha()
        self.version_dict[VersionType.LAST_UPDATE.value] = last_update
        self._set_properties(self.version_dict)
        with open(values.VERSION_CONFIG_PATH, "w") as toml_file:
            toml.dump(self.version_dict, toml_file)

    def create_version_file() -> None:
        last_update = Version._get_last_commit_sha()
        default_version_dict[VersionType.LAST_UPDATE.value] = last_update
        with open(values.VERSION_CONFIG_PATH, "w") as toml_file:
            toml.dump(default_version_dict, toml_file)

    def _set_properties(self, version_dict) -> None:
        self.version_dict = version_dict
        self.major = self.version_dict[VersionType.MAJOR.value]
        self.minor = self.version_dict[VersionType.MINOR.value]
        self.last_update = self.version_dict[VersionType.LAST_UPDATE.value]
        self.patch = self._calculate_patch()

    def _calculate_patch(self) -> int:
        return int(subprocess.check_output(['git', 'rev-list', f'{self.last_update}..HEAD', '--count']).decode('utf-8').strip())

    def _get_last_commit_sha() -> str:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode('utf-8').strip()
