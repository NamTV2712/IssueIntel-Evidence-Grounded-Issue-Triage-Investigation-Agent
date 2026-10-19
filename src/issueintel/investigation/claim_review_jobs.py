"""Product-only claim critique, admitted and receipted by the shared job runner."""
from .source_jobs import SourceJobs
from .claim_review import build_source,generate,validate,render_markdown


class ClaimReviewJobs(SourceJobs):
    mode='claim_review'
    depth='claims'
    confirmation='generate_claim_review_with_api_cost'
    database='claim_reviews.sqlite'
    run_directory='claim_review_runs'
    filename='claim-review.json'
    result_key='claim_review_result'
    history_key='review_jobs'
    build_source=staticmethod(build_source)
    generate=staticmethod(generate)
    validate=staticmethod(validate)
    render_markdown=staticmethod(render_markdown)
