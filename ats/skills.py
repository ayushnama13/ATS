"""Skill taxonomy with aliases, and a matcher that runs on resume/JD text."""
import re
from functools import lru_cache

# canonical -> aliases (matched case-insensitively on word boundaries)
TAXONOMY = {
    "python": ["python", "pandas", "numpy", "scipy"],
    "r": ["r programming", "rstudio", "ggplot", "tidyverse", "r language"],
    "sql": ["sql", "mysql", "postgresql", "postgres", "sqlite", "t-sql", "bigquery", "snowflake", "redshift"],
    "sas": ["sas"],
    "spark": ["spark", "pyspark", "databricks"],
    "hadoop": ["hadoop", "hive", "mapreduce"],
    "tableau": ["tableau"],
    "power bi": ["power bi", "powerbi"],
    "excel": ["excel", "vlookup", "pivot table", "pivot tables", "advanced excel", "google sheets"],
    "powerpoint": ["powerpoint", "ppt", "slides", "google slides"],
    "data visualization": ["data visualization", "data visualisation", "dashboard", "dashboards", "matplotlib", "seaborn", "plotly", "looker", "metabase", "d3.js"],
    "machine learning": ["machine learning", "scikit-learn", "sklearn", "xgboost", "lightgbm", "random forest", "gradient boosting", "svm", "classification", "supervised learning", "unsupervised learning"],
    "deep learning": ["deep learning", "neural network", "neural networks", "cnn", "rnn", "lstm", "transformer", "transformers", "pytorch", "tensorflow", "keras"],
    "nlp": ["nlp", "natural language processing", "text mining", "sentiment analysis", "bert", "tokenization", "named entity"],
    "computer vision": ["computer vision", "opencv", "image segmentation", "object detection", "yolo"],
    "genai / llm": ["genai", "generative ai", "gen ai", "llm", "llms", "large language model", "langchain", "rag", "retrieval augmented", "prompt engineering", "openai", "gemini api", "chatgpt", "fine-tuning", "fine tuning", "huggingface", "hugging face", "agentic", "vector database"],
    "statistics": ["statistics", "statistical", "hypothesis testing", "probability", "bayesian", "anova", "statistical inference", "stochastic"],
    "regression": ["regression", "linear regression", "logistic regression", "glm", "time series regression"],
    "forecasting": ["forecasting", "time series", "arima", "sarima", "prophet", "demand forecasting"],
    "a/b testing": ["a/b test", "a/b testing", "ab testing", "experimentation", "experiment design", "split testing", "bootstrapping", "causal inference"],
    "clustering": ["clustering", "cluster analysis", "k-means", "kmeans", "customer segmentation", "segmentation", "dbscan"],
    "optimization": ["optimization", "optimisation", "linear programming", "operations research", "integer programming", "gurobi", "pulp", "convex optimization"],
    "decision trees": ["decision tree", "decision trees", "cart"],
    "survival analysis": ["survival analysis", "kaplan", "cox"],
    "eda": ["exploratory data analysis", "eda", "data analysis", "data analytics", "analytics"],
    "data wrangling": ["data cleaning", "data wrangling", "data preprocessing", "etl", "data pipeline", "data pipelines", "feature engineering", "data modelling", "data modeling", "data warehouse"],
    "mlops / cloud": ["aws", "azure", "gcp", "google cloud", "docker", "kubernetes", "mlflow", "airflow", "sagemaker", "ci/cd", "mlops"],
    "c / c++": ["c++", "c/c++", "cpp"],
    "java": ["java"],
    "javascript": ["javascript", "typescript", "node.js", "nodejs", "react", "reactjs", "next.js", "vue", "angular", "html", "css"],
    "backend / apis": ["rest api", "rest apis", "flask", "django", "fastapi", "spring boot", "backend", "microservices", "graphql", "api development"],
    "git": ["git", "github", "gitlab"],
    "matlab": ["matlab", "simulink"],
    "app development": ["android", "ios", "flutter", "react native", "kotlin", "swift"],
    "product management": ["product management", "product manager", "product sense", "product strategy", "product roadmap", "roadmap", "prd", "product requirement", "user stories", "go-to-market", "gtm", "product thinking", "product case", "product design", "product development", "product analytics"],
    "user research": ["user research", "user interviews", "customer interviews", "usability", "ux research", "user empathy", "customer discovery", "personas", "user journey", "surveys"],
    "ux / design": ["figma", "ux", "ui/ux", "wireframe", "wireframes", "prototype", "prototyping", "design thinking", "sketch", "adobe xd"],
    "metrics / analytics": ["metrics", "kpi", "kpis", "funnel", "retention", "conversion", "engagement", "cohort", "mixpanel", "google analytics", "amplitude", "north star", "okr"],
    "marketing": ["marketing", "seo", "sem", "brand", "branding", "campaign", "campaigns", "social media", "growth", "content strategy", "performance marketing", "digital marketing"],
    "business / strategy": ["business analysis", "business strategy", "strategy", "consulting", "case study", "case competition", "market research", "business development", "go to market", "market analysis", "competitive analysis", "business model"],
    "operations": ["operations", "supply chain", "logistics", "process improvement", "lean", "six sigma"],
    "finance": ["finance", "financial modelling", "financial modeling", "valuation", "equity research", "quantitative finance", "portfolio", "trading", "derivatives", "risk"],
    "project management": ["project management", "agile", "scrum", "jira", "stakeholder", "stakeholders", "cross-functional", "cross functional", "program management", "kanban"],
    "leadership": ["led a team", "team lead", "lead", "captain", "head of", "coordinator", "secretary", "convener", "president", "vice president", "founder", "co-founder", "cofounder", "core team", "mentor", "manager"],
    "entrepreneurship": ["startup", "start-up", "founder", "co-founder", "cofounder", "e-cell", "entrepreneurship", "venture", "launched", "shipped", "built and launched", "mvp", "bootstrapped"],
    "communication": ["communication", "public speaking", "presentation", "presentations", "debate", "writing", "storytelling", "editor", "content writing", "copywriting", "blog"],
    "hardware / building": ["arduino", "raspberry pi", "embedded", "iot", "robotics", "pcb", "cad", "solidworks", "3d printing", "prototype", "hardware"],
    "competitive programming": ["codeforces", "codechef", "leetcode", "competitive programming", "icpc", "kaggle", "hackerrank", "gsoc", "hackathon", "hackathons"],
    "e-commerce": ["e-commerce", "ecommerce", "marketplace", "retail", "d2c", "consumer internet"],
}

_NON_WORD_L = r"(?<![A-Za-z0-9+#/])"
_NON_WORD_R = r"(?![A-Za-z0-9+#])"


@lru_cache(maxsize=None)
def _pattern(alias):
    return re.compile(_NON_WORD_L + re.escape(alias).replace(r"\ ", r"[\s\-]+") + _NON_WORD_R, re.I)


@lru_cache(maxsize=None)
def _skill_pattern(skill):
    aliases = TAXONOMY.get(skill, [skill])
    parts = [_pattern(a).pattern for a in sorted(set(aliases + [skill]), key=len, reverse=True)]
    return re.compile("|".join(parts), re.I)


def find_skills(text, skills=None):
    """Return {canonical_skill: hit_count} for skills present in text."""
    out = {}
    for s in (skills or TAXONOMY):
        n = len(_skill_pattern(s).findall(text))
        if n:
            out[s] = n
    return out


def has_skill(text, skill):
    return bool(_skill_pattern(skill).search(text))


def canonicalise(name):
    """Map a free-text skill name from the LLM onto the taxonomy when possible."""
    n = name.strip().lower()
    if n in TAXONOMY:
        return n
    for canon, aliases in TAXONOMY.items():
        if n in aliases:
            return canon
    for canon, aliases in TAXONOMY.items():
        if any(a == n or _pattern(a).fullmatch(n) for a in aliases):
            return canon
    return n
