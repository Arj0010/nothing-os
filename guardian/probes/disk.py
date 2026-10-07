import os

from ..models import DiskFacts


def probe_disk(mount: str = "/") -> DiskFacts:
    st = os.statvfs(mount)
    return DiskFacts(mount=mount, used=(st.f_blocks - st.f_bfree) * st.f_frsize, avail=st.f_bavail * st.f_frsize)
