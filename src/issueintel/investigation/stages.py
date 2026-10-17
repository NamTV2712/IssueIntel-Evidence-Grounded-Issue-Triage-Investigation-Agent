"""Private audit checkpoints; no automatic paid-call retry or recipe resume."""
from pathlib import Path
import hashlib

from issueintel.runtime.io import write_json


STAGES = {'prepared_input','seed_generation','focused_retrieval','refined_generation','verification_input','verification',
          'planned_input','planned_retrieval_1','planned_retrieval_2','planned_retrieval_3',
          'planned_generation_1','planned_generation_2','planned_generation_3','planned_complete'}


class StageCheckpoints:
    def __init__(self,root,directory):
        self.root=Path(root).resolve()
        self.directory=Path(directory).resolve()
        if not self.directory.is_relative_to(self.root/'artifacts'):
            raise ValueError('Stage checkpoints must remain inside artifacts/')
        self.receipts={}

    def __call__(self,stage,record):
        if stage not in STAGES or stage in self.receipts:
            raise ValueError('Invalid or repeated checkpoint stage')
        path=self.directory/(stage+'.json')
        if path.exists():
            raise ValueError('A completed stage must not be overwritten')
        write_json(path,record)
        ref={'path':path.relative_to(self.root).as_posix(),
             'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        self.receipts[stage]=ref
        return ref
