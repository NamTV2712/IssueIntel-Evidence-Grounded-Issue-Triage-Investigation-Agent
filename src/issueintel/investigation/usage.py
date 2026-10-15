"""Conservative per-job accounting from captured reservation receipts."""
from decimal import Decimal

from issueintel.runtime.io import digest


def job_usage(row):
    known=row.get('usage_tracking') is True or row.get('provider')=='none'
    receipts=sorted(row.get('usage_receipts',{}).values(),key=lambda r:(r['created_at'],r['reservation_id']))
    states={'reserved','settled','failed_bound','missing_usage_bound','breach'}
    for receipt in receipts:
        if (receipt['status'] not in states or any(type(receipt[key]) is not int or receipt[key]<0 for key in
                ('input_bound','output_bound','tokens_charged_or_reserved','micro_usd_charged_or_reserved'))):
            raise ValueError('Invalid captured usage receipt')
    tokens=sum(r['tokens_charged_or_reserved'] for r in receipts)
    micro_usd=sum(r['micro_usd_charged_or_reserved'] for r in receipts)
    result={'schema':'job_usage_v1','job_id':row['id'],'job_status':row['status'],
            'tracking_available':known,'currency':'USD','receipts':receipts,
            'reservations':len(receipts) if known else None,
            'tokens_charged_or_reserved':tokens if known else None,
            'usd_charged_or_reserved':str(Decimal(micro_usd)/1000000) if known else None,
            'all_usage_settled':known and all(r['status']=='settled' for r in receipts),
            'retained_bounds':sum(r['status'] in {'reserved','failed_bound','missing_usage_bound'} for r in receipts),
            'usage_anomalies':sum(r['status']=='breach' for r in receipts),
            'is_provider_invoice':False,'captured_receipts_only':True,
            'limitations':['Reserved/failed/missing-usage calls retain conservative bounds, not confirmed invoice amounts.',
                'An interrupted process may retain the last captured reservation state.',
                'Legacy jobs without captured receipts remain unknown; global ledger totals are not attributed to them.']}
    result['sha256']=digest(result)
    return result
