"""Product check-plan adapter for shared durable source jobs."""
from .source_jobs import SourceJobs
from .check_plans import build_source,generate_plan,validate_plan,render_markdown

CONFIRMATION='generate_check_plan_with_api_cost'


class CheckPlanJobs(SourceJobs):
    mode='check_plan'
    depth='checks'
    confirmation=CONFIRMATION
    database='check_plans.sqlite'
    run_directory='check_plan_runs'
    filename='check-plan.json'
    result_key='check_plan_result'
    history_key='plan_jobs'
    build_source=staticmethod(build_source)
    generate=staticmethod(generate_plan)
    validate=staticmethod(validate_plan)
    render_markdown=staticmethod(render_markdown)
