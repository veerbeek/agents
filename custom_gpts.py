import os
import sys
import json
import openai 

from time import sleep

class ReporterGPT:
	def __init__(self, client, role, model="gpt-4-turbo-preview", project=None, dataset=None, outdir=None, other_files=None):
		instructions = open(f'prompts/roles/{role}.txt', 'r').read()

		assistants = client.beta.assistants.list()
		assistant_names = [assistant.name for assistant in assistants]
		
		assistant_name = role + '-' + project
		if assistant_name not in assistant_names:
			if dataset is not None:
				self.assistant = client.beta.assistants.create(
				name=assistant_name,
				instructions=instructions,
				model=model,
				tools=[{"type": "code_interpreter"}],
				tool_resources={'code_interpreter': {'file_ids': [dataset.id]}}
				)
			elif other_files is not None:
				vector_store = client.beta.vector_stores.create(
 								 name="Editor documents",
  								file_ids=[file.id for file in other_files]
									)
				self.assistant = client.beta.assistants.create(
				name=assistant_name,
				instructions=instructions,
				model=model,
				tools=[{"type": "file_search"}],
				tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}})

		else:
			self.assistant = [assistant for assistant in assistants if assistant.name == assistant_name][0]

		self.role = role
		self.outdir = outdir
		self.thread = client.beta.threads.create()
		self.client = client

	def log_messages(self, run_id):
		log_outdir = os.path.join(self.outdir, 'logs')
		if not os.path.exists(log_outdir):
			os.mkdir(log_outdir)
		agent_outdir = os.path.join(log_outdir, self.role)
		if not os.path.exists(agent_outdir):
			os.mkdir(agent_outdir)

		run_outdir = os.path.join(agent_outdir, run_id)
		if not os.path.exists(run_outdir):
			os.mkdir(run_outdir)

		with open(os.path.join(run_outdir, 'messages.json'), 'w') as file:
			json.dump(json.loads(self.messages.json()), file, indent=4)
		with open(os.path.join(run_outdir, 'steps.json'), 'w') as file:
			json.dump(json.loads(self.run_steps.json()), file, indent=4)

	def get_first_text_content(self, message):
	    for content_block in message.content:
	        if content_block.type == "text":
	            return content_block.text.value
	    return None

	def message(self, message_text):
		message = self.client.beta.threads.messages.create(
			thread_id=self.thread.id,
			role="user",
			content=message_text)
		
		run = self.client.beta.threads.runs.create(
			thread_id=self.thread.id,
			assistant_id=self.assistant.id)
		
		while run.status == 'in_progress' or run.status == 'queued':
			run = self.client.beta.threads.runs.retrieve(thread_id=self.thread.id, run_id=run.id)
			sleep(0.1)
		
		self.messages = self.client.beta.threads.messages.list(
		  thread_id=self.thread.id,
		  run_id=run.id
				)

		self.run_steps = self.client.beta.threads.runs.steps.list(
 				 thread_id=self.thread.id,
 				 run_id=run.id
  				)

		self.log_messages(run.id)

		for message in self.messages:
			return_message = self.get_first_text_content(message)
			if return_message is None:
				return_message = ""
			return return_message



