To run the agents pipeline:

```{python}
from ddj_agents import AgentsPipeline, Baseline

OPENAI_KEY = input()

PROJECT_ID = 'civio-emergency'
DATASET = 'datasets/civio-emergency/contracts_combined.csv'
DATASET_DESCRIPTION = 'datasets/civio-emergency/description.md'

baseline = Baseline(api_key=OPENAI_KEY,
						project_id=PROJECT_ID, 
						dataset=DATASET, 
						dataset_description=DATASET_DESCRIPTION)
baseline.run()

agents = AgentsPipeline(api_key=OPENAI_KEY,
						project_id=PROJECT_ID, 
						dataset=DATASET, 
						dataset_description=DATASET_DESCRIPTION, 
						 use_reporter=True,
						  use_editor=True)
agents.run()
```
