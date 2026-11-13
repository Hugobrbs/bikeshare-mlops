import io
import zipfile
from pathlib import Path

import httpx
import pytest

from bikeshare.data.acquisition import (
    AcquisitionError,
    IntegrityError,
    fetch_source,
    sha256_bytes,
)
from bikeshare.pipeline_config import DatasetConfig

CONTENT = b"instant,dteday,cnt\n1,2011-01-01,16\n"
PRIMARY = "https://primary.test/data.zip"
MIRROR = "https://mirror.test/data.zip"


def make_zip(content: bytes = CONTENT, member: str = "hour.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(member, content)
    return buffer.getvalue()


def make_dataset(urls: list[str] | None = None) -> DatasetConfig:
    return DatasetConfig(
        name="test",
        member="hour.csv",
        member_sha256=sha256_bytes(CONTENT),
        urls=urls or [PRIMARY, MIRROR],
    )


class FakeServer:
    def __init__(self, responses: dict[str, httpx.Response | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.host)
        response = self.responses[request.url.host]
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def test_download_and_verify(tmp_path: Path) -> None:
    server = FakeServer({"primary.test": httpx.Response(200, content=make_zip())})

    snapshot = fetch_source(make_dataset(), tmp_path, transport=server.transport)

    assert snapshot.path.read_bytes() == CONTENT
    assert snapshot.origin == PRIMARY
    assert (tmp_path / "manifest.json").is_file()


def test_second_call_does_not_download_again(tmp_path: Path) -> None:
    server = FakeServer({"primary.test": httpx.Response(200, content=make_zip())})
    fetch_source(make_dataset(), tmp_path, transport=server.transport)

    snapshot = fetch_source(make_dataset(), tmp_path, transport=server.transport)

    assert server.calls == ["primary.test"]
    assert snapshot.origin == PRIMARY


def test_falls_back_to_mirror(tmp_path: Path) -> None:
    server = FakeServer(
        {
            "primary.test": httpx.Response(503),
            "mirror.test": httpx.Response(200, content=make_zip()),
        }
    )

    snapshot = fetch_source(make_dataset(), tmp_path, transport=server.transport)

    assert server.calls == ["primary.test", "mirror.test"]
    assert snapshot.origin == MIRROR


def test_corrupted_local_copy_is_replaced(tmp_path: Path) -> None:
    (tmp_path / "hour.csv").write_bytes(b"fichier tronque")
    server = FakeServer({"primary.test": httpx.Response(200, content=make_zip())})

    snapshot = fetch_source(make_dataset(), tmp_path, transport=server.transport)

    assert snapshot.path.read_bytes() == CONTENT


@pytest.mark.parametrize(
    ("primary", "mirror", "transient"),
    [
        (httpx.Response(503), httpx.ConnectError("refused"), True),
        (httpx.Response(404), httpx.Response(403), False),
    ],
)
def test_all_sources_down(
    tmp_path: Path, primary: httpx.Response, mirror: httpx.Response | Exception, transient: bool
) -> None:
    server = FakeServer({"primary.test": primary, "mirror.test": mirror})

    with pytest.raises(AcquisitionError) as error:
        fetch_source(make_dataset(), tmp_path, transport=server.transport)

    assert error.value.transient is transient
    assert not (tmp_path / "hour.csv").exists()


@pytest.mark.parametrize(
    "archive",
    [make_zip(b"autre contenu"), make_zip(member="day.csv"), b"<html>maintenance</html>"],
    ids=["mauvaise-empreinte", "fichier-absent", "pas-un-zip"],
)
def test_wrong_content_is_never_saved(tmp_path: Path, archive: bytes) -> None:
    server = FakeServer({"primary.test": httpx.Response(200, content=archive)})

    with pytest.raises(IntegrityError):
        fetch_source(make_dataset([PRIMARY]), tmp_path, transport=server.transport)

    assert not (tmp_path / "hour.csv").exists()
