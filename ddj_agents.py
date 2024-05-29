import re
import os
import openai

from tqdm.auto import tqdm
from custom_gpts import ReporterGPT

class AgentsPipeline:
    def __init__(self, api_key, project_id, dataset, dataset_description, n_questions=10, 
                n_bullets=10, max_feedback=3, use_reporter=True, use_editor=True, reset_agents=True):
        self.api_key = api_key
        self.project_id = project_id
        self.dataset = dataset
        self.dataset_description = dataset_description
        self.n_questions = n_questions
        self.n_bullets = n_bullets
        self.max_feedback = max_feedback
        self.use_reporter = use_reporter
        self.use_editor = use_editor
        self.reset_agents = reset_agents

        self.client = openai.OpenAI(api_key=self.api_key)
        self.outdir = os.path.join('outputs', self.project_id + '-analyst')
        if self.use_reporter:
            self.outdir += '-reporter'
        if self.use_editor:
            self.outdir += '-editor'
        if not os.path.exists(self.outdir):
            os.mkdir(self.outdir)

        self.file = self.retrieve_file(self.dataset)
        self.analyst = ReporterGPT(self.client, role='analyst', dataset=self.file, project=self.project_id, outdir=self.outdir)
        self.editor = None
        self.reporter = None

        if self.use_editor:
            editor_docs = [self.retrieve_file(file.path) for file in os.scandir('editor_docs') if '.txt' in file.path]
            self.editor = ReporterGPT(self.client, role='editor', project=self.project_id, outdir=self.outdir, other_files=editor_docs)
        
        if self.use_reporter:
            self.reporter = ReporterGPT(self.client, role='reporter', dataset=self.file, project=self.project_id, outdir=self.outdir)

    def retrieve_file(self, file_path):
        filename = file_path.split('/')[-1]
        existing_files = [file for file in self.client.files.list()]
        existing_filenames = [file.filename for file in existing_files]

        if filename not in existing_filenames:
            file = self.client.files.create(file=open(file_path, "rb"), purpose='assistants')
        else:
            file = [file for file in existing_files if file.filename == filename][0]
        return file

    def brainstorm_questions(self):
        agent = self.reporter if self.use_reporter else self.analyst
        question_prompt = open('prompts/tasks/STEP_1_brainstorm_questions.txt').read().format(self.n_questions)
        question_prompt = open(self.dataset_description, 'r').read() + '\n\n' + question_prompt
        
        questions_outfile = os.path.join(self.outdir, 'questions.txt')
        if not os.path.exists(questions_outfile):
            questions_raw = agent.message(question_prompt)
            questions = [question for question in questions_raw.split('\n\n') if re.match(r"^\d+\.", question)]
            if len(questions) == 0:
                raise Exception("No questions generated.")
            with open(questions_outfile, 'w') as f:
                for question in questions:
                    f.write(question + '\n\n')
        else:
            questions = open(questions_outfile, 'r').read().split('\n\n')
        return questions

    def write_plan(self, i, question_str):
        question_outdir = os.path.join(self.outdir, f'{i+1}')
        if not os.path.exists(question_outdir):
            os.mkdir(question_outdir)

        plan_outfile = os.path.join(question_outdir, 'analytical_plan.txt')
        if not os.path.exists(plan_outfile):
            dataset_description = open(self.dataset_description, 'r').read()
            analytical_plan = self.analyst.message(open('prompts/tasks/STEP_2_analytical_plan.txt').read().format(dataset_description, question_str))
            if self.use_editor:
                editor_prompt = dataset_description + '\n#Task\n' + open('prompts/tasks/STEP_2_editor_plan_feedback.txt').read().format(question_str, analytical_plan)
                feedback = self.editor.message(editor_prompt)
                feedback_outfile = os.path.join(question_outdir, 'plan_editor_feedback.txt')
                with open(feedback_outfile, 'w') as f:
                    f.write(feedback)

                analytical_plan = self.analyst.message(open('prompts/tasks/STEP_2_implement_editor_feedback.txt').read().format(feedback))
            with open(plan_outfile, 'w') as f:
                f.write(analytical_plan)
        else:
            analytical_plan = open(plan_outfile, 'r').read()
        return analytical_plan

    def execute_analysis(self, i, analytical_plan, question_str):
        question_outdir = os.path.join(self.outdir, f'{i+1}')
        all_summaries = []
        summary = self.analyst.message(open('prompts/tasks/STEP_3a_execute_plan.txt').read().format(analytical_plan))
        
        with open(os.path.join(question_outdir, '1_execution.txt'), 'w') as f:
            f.write(summary)

        summary = self.analyst.message('Summarize your approach and the key findings in bullet points.')
        with open(os.path.join(question_outdir, '1_analysis.txt'), 'w') as f:
            f.write(summary)

        all_summaries.append(summary)
        
        if self.use_reporter:
            feedback_counter = 0
            while feedback_counter < self.max_feedback:
                if self.reset_agents:
                    dataset_description = open(self.dataset_description, 'r').read()
                    feedback_prompt = "\n#Task\n" + open('prompts/tasks/STEP_3b_execution_feedback_reporter.txt').read().format(question_str, summary)
                else:
                    feedback_prompt = open('prompts/tasks/STEP_3b_execution_feedback_reporter.txt').read().format(question_str, summary)
                feedback = self.reporter.message(feedback_prompt)
                with open(os.path.join(question_outdir, f'{feedback_counter + 1}_feedback.txt'), 'w') as f:
                    f.write(feedback)
                if 'Option 1' in feedback:
                    return all_summaries
                elif 'Option 2' in feedback:
                    summary = self.analyst.message(open('prompts/tasks/STEP_3c_implement_reporter_feedback.txt').read().format(feedback))
                    with open(os.path.join(question_outdir, f'{feedback_counter + 2}_execution.txt'), 'w') as f:
                        f.write(summary)
                    summary = self.analyst.message("Summarize your revised approach and the key findings in bullet points")
                    with open(os.path.join(question_outdir, f'{feedback_counter + 2}_analysis.txt'), 'w') as f:
                        f.write(summary)
                    all_summaries.append(summary)
                elif 'Option 3' in feedback:
                    return None
                else:
                    break
                feedback_counter += 1
        else:
            return all_summaries
        return None

    def summarize_newsworthy_insights(self, i, question, summaries):
        summary = "\n".join(summaries)
        question_outdir = os.path.join(self.outdir, f'{i+1}')
        message = open('prompts/tasks/STEP_3f_summarize_insights.txt', 'r').read().format(question, summary)

        if self.use_reporter:
            summary = self.reporter.message(message)
        else:
            summary = self.analyst.message(message)

        with open(os.path.join(question_outdir, f'bullets.txt'), 'w') as f:
            f.write(summary)
        return summary

    def create_tipsheet(self):
        prompt = ""
        for question in range(self.n_questions):
            question_outdir = os.path.join(self.outdir, f'{question+1}')
            bullets = os.path.join(question_outdir, 'bullets.txt')
            if os.path.exists(bullets):
                bullets = open(bullets, 'r').read()
                prompt += f"""```
                            Analysis [{question + 1}]

                            {bullets}
                            ````
                            """
        prompt = open('prompts/tasks/STEP_4_create_tipsheet.txt', 'r').read().format(self.n_bullets, prompt)
        if self.use_reporter:
            tipsheet = self.reporter.message(prompt)
        else:
            tipsheet = self.analyst.message(prompt)
        return tipsheet

    def run(self):
        print('Starting run...')
        
        questions = self.brainstorm_questions()
        all_summaries = []

        for i, question in enumerate(tqdm(questions)):
            if i > self.n_questions:
                break
            question_outdir = os.path.join(self.outdir, f'{i+1}')
            if self.reset_agents:
                self.analyst = ReporterGPT(self.client, role='analyst', dataset=self.file, project=self.project_id, outdir=self.outdir)
                if self.use_editor:
                    editor_docs = [self.retrieve_file(file.path) for file in os.scandir('editor_docs') if '.txt' in file.path]
                    self.editor = ReporterGPT(self.client, role='editor', project=self.project_id, outdir=self.outdir, other_files=editor_docs)
                if self.use_reporter:
                    self.reporter = ReporterGPT(self.client, role='reporter', dataset=self.file, project=self.project_id, outdir=self.outdir)

            analytical_plan = self.write_plan(i, question)
            analysis = self.execute_analysis(i, analytical_plan, question)
            if analysis is not None:
                bullets = self.summarize_newsworthy_insights(i, question, analysis)
                if self.use_editor:
                    feedback = self.editor.message(open('prompts/tasks/STEP_3d_editor_feedback.txt').read().format(question, bullets))
                    with open(os.path.join(question_outdir, 'editor_feedback.txt'), 'w') as f:
                        f.write(feedback)

                    new_insights = self.analyst.message(open('prompts/tasks/STEP_3e_implement_editor_feedback.txt').read().format(feedback))
                    with open(os.path.join(question_outdir, 'final_revision.txt'), 'w') as f:
                        f.write(new_insights)

                    summary = self.analyst.message("Summarize your revised approach and the key findings in bullet points")
                    with open(os.path.join(question_outdir, 'final_analysis.txt'), 'w') as f:
                        f.write(summary)

                    analysis.append(summary)
                    bullets = self.summarize_newsworthy_insights(i, question, analysis)

        tipsheet = self.create_tipsheet()
        with open(os.path.join(self.outdir, 'tipsheet.txt'), 'w') as f:
            f.write(tipsheet)

class Baseline:
    def __init__(self, api_key, project_id, dataset, dataset_description, n_questions=10, n_bullets=10, reset_agents=True):
        self.api_key = api_key
        self.project_id = project_id
        self.dataset = dataset
        self.dataset_description = dataset_description
        self.n_questions = n_questions
        self.n_bullets = n_bullets
        self.reset_agents = reset_agents

        self.client = openai.OpenAI(api_key=self.api_key)
        self.outdir = os.path.join('outputs', self.project_id + '-baseline')
        if not os.path.exists(self.outdir):
            os.mkdir(self.outdir)

        self.file = self.retrieve_file(self.dataset)
        self.analyst = ReporterGPT(self.client, role='baseline', dataset=self.file, project=self.project_id, outdir=self.outdir)

    def retrieve_file(self, file_path):
        filename = file_path.split('/')[-1]
        existing_files = [file for file in self.client.files.list()]
        existing_filenames = [file.filename for file in existing_files]

        if filename not in existing_filenames:
            file = self.client.files.create(file=open(file_path, "rb"), purpose='assistants')
        else:
            file = [file for file in existing_files if file.filename == filename][0]
        return file

    def brainstorm_questions(self):
        question_prompt = open('prompts/tasks/STEP_1_brainstorm_questions.txt').read().format(self.n_questions)
        question_prompt = open(self.dataset_description, 'r').read() + '\n\n' + question_prompt
        
        questions_outfile = os.path.join(self.outdir, 'questions.txt')
        if not os.path.exists(questions_outfile):
            questions_raw = self.analyst.message(question_prompt)
            questions = [question for question in questions_raw.split('\n\n') if re.match(r"^\d+\.", question)]
            if len(questions) == 0:
                raise Exception("No questions generated.")
            with open(questions_outfile, 'w') as f:
                for question in questions:
                    f.write(question + '\n\n')
        else:
            questions = open(questions_outfile, 'r').read().split('\n\n')
        return questions

    def execute_analysis(self, i, question_str):
        question_outdir = os.path.join(self.outdir, f'{i+1}')
        if not os.path.exists(question_outdir):
            os.mkdir(question_outdir)
        all_summaries = []
        analysis_prompt = """
        ## Task 

        Try to answer the following question using the provided dataset:
        ```
        {}
        ```

        Carry out the analysis and return its insights.
        """.format(question_str)

        analysis_prompt = open(self.dataset_description, 'r').read() + '\n\n' + analysis_prompt

        summary = self.analyst.message(analysis_prompt)
        if summary is None:
            return None
        with open(os.path.join(question_outdir, '1_execution.txt'), 'w') as f:
            f.write(summary)

        summary = self.analyst.message('Summarize your approach and the key findings in bullet points.')
        if summary is None:
            return None
        with open(os.path.join(question_outdir, 'bullets.txt'), 'w') as f:
            f.write(summary)

        all_summaries.append(summary)
        return all_summaries

    def create_tipsheet(self):
        prompt = ""
        for question in range(self.n_questions):
            question_outdir = os.path.join(self.outdir, f'{question+1}')
            
            bullets = os.path.join(question_outdir, 'bullets.txt')
            if os.path.exists(bullets):
                bullets = open(bullets, 'r').read()
                prompt += f"""```
                        Analysis [{question + 1}]

                        {bullets}
                        ```
                        """
        prompt = open('prompts/tasks/STEP_4_create_tipsheet.txt', 'r').read().format(self.n_bullets, prompt)
        tipsheet = self.analyst.message(prompt)
        return tipsheet

    def run(self):
        print('Starting run...')

        questions = self.brainstorm_questions()

        for i, question in enumerate(tqdm(questions)):
            if i > self.n_questions:
                break
            if self.reset_agents:
                self.analyst = ReporterGPT(self.client, role='baseline', dataset=self.file, project=self.project_id, outdir=self.outdir)
            summaries = self.execute_analysis(i, question)

        if self.reset_agents:
            self.analyst = ReporterGPT(self.client, role='baseline', dataset=self.file, project=self.project_id, outdir=self.outdir)
        tipsheet = self.create_tipsheet()

        with open(os.path.join(self.outdir, 'tipsheet.txt'), 'w') as f:
            f.write(tipsheet)