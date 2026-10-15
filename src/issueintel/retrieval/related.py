"""Bounded lexical issue candidates, separate from strict inference evidence."""
from collections import Counter
import hashlib
import math
import re
import unicodedata


STOP = set('a an the is are was were be to of and or for in on at with this that it i we you can could should would issue error lỗi và là của với các một này khi không'.split())


def words(text):
    normalized = unicodedata.normalize('NFKC', text).casefold()
    return [word for token in re.findall(r'\w+', normalized) for word in token.split('_')
            if len(word)>1 and not word.isdigit() and word not in STOP]


def rank_related(query, documents, *, limit=5):
    if not isinstance(query,str) or not query.strip() or len(query)>120000:
        raise ValueError('A bounded issue query is required')
    if type(limit) is not int or not 1<=limit<=10 or not isinstance(documents,list) or len(documents)>50:
        raise ValueError('Related search is bounded to 50 documents and 10 results')
    terms=set(words(query))
    prepared=[]
    identities=set()
    for document in documents:
        text=document['text']
        if not isinstance(text,str) or len(text)>120000 or document['job_id'] in identities:
            raise ValueError('Invalid or duplicate related document')
        identities.add(document['job_id'])
        if hashlib.sha256(text.encode()).hexdigest()!=document['query_sha256']:
            raise ValueError('Related query bytes changed')
        prepared.append((document,Counter(words(text))))
    frequencies=Counter(word for _,counts in prepared for word in counts)
    average=sum(sum(counts.values()) for _,counts in prepared)/max(1,len(prepared)) or 1
    results=[]
    for document,counts in prepared:
        shared=terms & counts.keys()
        if len(shared)<2:
            continue
        length=sum(counts.values())
        score=sum(math.log(1+(len(prepared)-frequencies[word]+.5)/(frequencies[word]+.5))
                  * counts[word]*2.2/(counts[word]+1.2*(.25+.75*length/average)) for word in sorted(shared))
        lines=document['text'].splitlines()
        line_number=next((i for i,line in enumerate(lines,1) if set(words(line)) & shared),1)
        line=lines[line_number-1] if lines else ''
        excerpt=line[:500]
        results.append({key:document[key] for key in ('job_id','case_id','repository','pack_sha256','query_sha256')}
                       | {'score':round(score,6), 'score_is_probability':False,
                          'matched_terms':sorted(shared)[:20],
                          'snippet':{'line':line_number,'text':excerpt,'truncated':len(line)>500,
                                     'text_sha256':hashlib.sha256(excerpt.encode()).hexdigest(),
                                     'source_line_sha256':hashlib.sha256(line.encode()).hexdigest()},
                          'relationship':'candidate_unverified'})
    return sorted(results,key=lambda row:(-row['score'],row['case_id'],row['job_id']))[:limit]
