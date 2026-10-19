import io
import hashlib
from botocore.exceptions import ClientError

class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.offline = False
    def get_object(self, *, Bucket, Key):
        if self.offline:
            raise OSError("offline")
        self.reads.append(Key)
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        payload = self.objects[Key]
        return {"Body": io.BytesIO(payload), "ETag": '"' + hashlib.sha256(payload).hexdigest() + '"'}
