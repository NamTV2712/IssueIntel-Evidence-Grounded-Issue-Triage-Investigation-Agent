"""Pure ownership replay for saved Typesafe attempts, decisions and receipts."""
from copy import deepcopy
from hashlib import sha256

from issueintel.runtime.typesafe import validate_receipt


class UnsettledControllerReceipt(ValueError):pass


def segments(rows,context):
    origin=context['controller']['origin_job_id'];count=0;records=[];result=[];seen=set();orphans=0
    if not rows or rows[0]['id']!=origin:raise ValueError('Changed controller origin')
    for row in rows:
        owner=row['id'];attempts=row.get('controller_attempts');decisions=row.get('typesafe_decisions');receipts=row.get('typesafe_receipts')
        if not isinstance(attempts,list) or not isinstance(decisions,list) or not isinstance(receipts,dict):
            raise ValueError('Missing durable controller accounting')
        if not len(decisions)<=len(attempts)<=len(decisions)+1:
            raise ValueError('Controller attempts cannot advance beyond a missing decision')
        slots={}
        for offset,attempt in enumerate(attempts,1):
            count+=1;index=attempt.get('decision_index')
            if (set(attempt)!={'schema','context_sha256','stage','controller_calls','max_controller_calls','decision_index','owner_job_id'}
                    or attempt['schema']!='product_typesafe_call_attempt_v1' or attempt['context_sha256']!=row['pipeline_context_sha256']
                    or attempt['controller_calls']!=count or type(attempt['controller_calls']) is not int or count>4
                    or type(attempt['max_controller_calls']) is not int or attempt['max_controller_calls']!=4
                    or type(index) is not int or index!=len(records)+offset or not 1<=index<=4
                    or attempt['stage']!='planned_decision_'+str(index) or attempt['owner_job_id']!=owner or index in slots):
                raise ValueError('Changed controller attempt owner or global bound')
            slots[index]=attempt
        owned={}
        for identifier,receipt in receipts.items():
            validate_receipt(receipt)
            binding=receipt['binding']
            if (identifier!=receipt['reservation_id'] or identifier in seen or binding['job_id']!=owner
                    or binding['decision_index'] not in slots or binding['decision_index'] in owned
                    or binding['query_sha256']!=sha256(context['case']['problem_statement'].encode()).hexdigest()
                    or binding['snapshot_sha256']!=context['snapshot']['snapshot_sha256']
                    or binding['commit_sha']!=context['snapshot']['commit_sha']):
                raise ValueError('Changed controller receipt ownership')
            if receipt['status']=='reserved':raise UnsettledControllerReceipt('Controller reservation requires accounting recovery')
            owned[binding['decision_index']]=receipt;seen.add(identifier)
        for record in decisions:
            index=len(records)+1
            if (record['decision_index']!=index or record['binding']['decision_index']!=index
                    or record['binding']['job_id']!=owner or index not in slots
                    or record['receipt']!=owned.get(index)):
                raise ValueError('Changed completed controller decision ownership')
            records.append(record)
        orphans+=sum(index not in {r['decision_index'] for r in decisions} for index in owned)
        result.append({'job_id':owner,'attempts':deepcopy(attempts),'decisions':deepcopy(decisions),'receipts':deepcopy(receipts)})
    if len(records)>4:raise ValueError('Changed completed controller bound')
    return result,records,count,orphans
