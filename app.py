# ==============================================================================
# FILE: app.py (UPDATED VERSION with Manual Mark Entry)
# ==============================================================================

import os
import base64
import random
import re
import fitz  # PyMuPDF
from flask import Flask, render_template, request, abort

app = Flask(__name__)

# --- Helper Functions ---

def encode_image_to_base64(file_storage):
    if not file_storage:
        return None
    try:
        encoded_string = base64.b64encode(file_storage.read()).decode('utf-8')
        return f"data:{file_storage.mimetype};base64,{encoded_string}"
    except Exception as e:
        print(f"Error encoding image: {e}")
        return None

def parse_question_bank_pdf(pdf_path):
    questions_pool = []
    co_descriptions = {}
    q_counter = 1
    
    try:
        doc = fitz.open(pdf_path)
        
        for page in doc:
            tables = page.find_tables()
            for table in tables:
                extracted = table.extract()
                if not extracted or len(extracted[0]) < 2: continue
                
                header = "".join(str(cell) for cell in extracted[0]).lower()
                first_col_content = "".join(str(row[0]) for row in extracted).upper()

                if "outcome" in header or "cos" in header or "CO" in first_col_content:
                    start_row = 1 if "outcome" in header or "cos" in header else 0
                    for row in extracted[start_row:]:
                        co_num_raw = str(row[0])
                        co_desc_raw = str(row[1])
                        if "CO" in co_num_raw and len(co_desc_raw) > 10:
                            co_num = "CO" + "".join(filter(str.isdigit, co_num_raw))
                            co_descriptions[co_num] = co_desc_raw.strip()
        
        for page in doc:
            tables = page.find_tables()
            for table in tables:
                extracted_table = table.extract()
                for row in extracted_table[1:]:
                    if len(row) >= 5 and row[0] is not None:
                        question_text = str(row[1]).replace('\n', ' ').strip()
                        marks_str = str(row[2]).strip()
                        if question_text and marks_str.isdigit():
                            questions_pool.append({
                                'id': q_counter,
                                'text': question_text,
                                'marks': int(marks_str),
                                'co': str(row[3]).strip().replace(" ",""),
                                'rbt': str(row[4]).strip()
                            })
                            q_counter += 1
        
        if not co_descriptions:
            print("No CO table found. Falling back to text scan for Course Outcomes.")
            full_text_for_cos = "".join(page.get_text() for page in doc)
            co_pattern = re.compile(r"^(CO\s*\d+)\s*(.*)", re.MULTILINE)
            for match in co_pattern.finditer(full_text_for_cos):
                co_num = match.group(1).replace(" ", "")
                co_desc = match.group(2).strip()
                if co_desc and len(co_desc) > 10:
                    co_descriptions[co_num] = co_desc

    except Exception as e:
        print(f"Error parsing PDF: {e}")
        return [], {}
        
    return questions_pool, co_descriptions


# --- Flask Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate_paper():
    try:
        form_data = request.form.to_dict()
        
        question_pdf_file = request.files.get('question_pdf')
        if not question_pdf_file:
            return "Question Bank PDF is required.", 400

        pdf_path = os.path.join("temp_qb.pdf")
        question_pdf_file.save(pdf_path)
        
        form_data['logo_encoded'] = encode_image_to_base64(request.files.get('logo_image'))
        form_data['prepared_by_sig_encoded'] = encode_image_to_base64(request.files.get('prepared_by_sig'))
        form_data['principal_sig_encoded'] = encode_image_to_base64(request.files.get('principal_sig'))
        form_data['hod_sig_encoded'] = encode_image_to_base64(request.files.get('hod_sig'))

        questions_pool, co_descriptions = parse_question_bank_pdf(pdf_path)
        os.remove(pdf_path)

        if not questions_pool:
            return "Could not parse any questions from the provided PDF. Please check the PDF format.", 400

        # --- MODIFIED: Logic for Equal Mark Weightage using manual input ---
        # 1. Determine the target marks for the paper (based on one valid path: Q1, Q3, Q5)
        total_paper_marks = 0
        all_cos_in_pool = {q['co'] for q in questions_pool if q['co']}
        num_cos = len(all_cos_in_pool)

        for i in range(1, 7, 2):  # Check Q1, Q3, Q5 to determine one path's total marks
            marks_string = form_data.get(f'q{i}_marks', '')
            if marks_string:
                try:
                    # Parse the comma-separated string into a list of integers
                    marks_list = [int(m.strip()) for m in marks_string.split(',') if m.strip()]
                    total_paper_marks += sum(marks_list)
                except ValueError:
                    return f"Invalid marks format for Question {i}: '{marks_string}'. Please use comma-separated numbers.", 400

        target_marks_per_co = total_paper_marks / num_cos if num_cos > 0 else 0
        co_marks_so_far = {co: 0 for co in all_cos_in_pool}
        
        configured_questions = {}
        used_question_ids = set()
        
        for i in range(1, 7):
            # MODIFIED: Get marks from the text input field
            marks_string = form_data.get(f'q{i}_marks', '')
            
            marks_list = []
            if marks_string:
                try:
                    # MODIFIED: Parse the comma-separated string into a list of integers
                    marks_list = [int(m.strip()) for m in marks_string.split(',') if m.strip()]
                except ValueError:
                     return f"Invalid marks format for Question {i}: '{marks_string}'. Please use comma-separated numbers only.", 400

            if not marks_list:
                continue

            main_question_data = {
                'number': str(i), 'sub_questions': [], 'total_marks': sum(marks_list),
                'main_co': set(), 'main_rbt': set()
            }

            for sub_index, marks in enumerate(marks_list):
                eligible_by_mark = [q for q in questions_pool if q['marks'] == marks and q['id'] not in used_question_ids]
                
                if not eligible_by_mark:
                    return f"Error: Could not find an unused question worth {marks} marks for Q{i}. Please add more questions with these marks to your question bank.", 400

                # 2. Define a "cost" function to score each eligible question
                def calculate_cost(q):
                    current_marks = co_marks_so_far.get(q['co'], 0)
                    future_marks = current_marks + q['marks']
                    # The cost is how far the new total for that CO would be from the ideal target
                    return abs(future_marks - target_marks_per_co)
                
                # 3. Sort eligible questions by this cost to find the one that best balances the paper
                eligible_by_mark.sort(key=calculate_cost)
                
                selected_q = eligible_by_mark[0]
                
                # 4. Update tracking
                used_question_ids.add(selected_q['id'])
                if selected_q['co'] in co_marks_so_far:
                    co_marks_so_far[selected_q['co']] += selected_q['marks']
                
                main_question_data['sub_questions'].append({
                    'letter': chr(97 + sub_index), 'text': selected_q['text'],
                    'marks': selected_q['marks'], 'co': selected_q['co'], 'rbt': selected_q['rbt']
                })
                main_question_data['main_co'].add(selected_q['co'])
                main_question_data['main_rbt'].add(selected_q['rbt'])
            
            configured_questions[f'q{i}'] = main_question_data

        final_paper_questions = []
        for i in range(1, 7):
            if f'q{i}' in configured_questions:
                final_paper_questions.append(configured_questions[f'q{i}'])

        # Calculate final totals for the summary table based on one path (Q1, Q3, Q5)
        co_totals = {co: 0 for co in co_descriptions.keys()}
        rbt_totals = {}
        for index, q_data in enumerate(final_paper_questions):
            if index % 2 == 0:  # This selects Q1 (index 0), Q3 (index 2), Q5 (index 4)
                for sub_q in q_data['sub_questions']:
                    if sub_q['co'] in co_totals:
                        co_totals[sub_q['co']] += sub_q['marks']
                    else:
                        co_totals[sub_q['co']] = sub_q['marks']
                    
                    rbt_totals[sub_q['rbt']] = rbt_totals.get(sub_q['rbt'], 0) + sub_q['marks']
        
        co_outcomes_for_template = []
        for co_num, co_desc in co_descriptions.items():
            if co_totals.get(co_num, 0) > 0:
                 co_outcomes_for_template.append({
                    'number': co_num,
                    'description': co_desc,
                    'marks': co_totals.get(co_num)
                })

        return render_template('paper.html', data=form_data, paper_questions=final_paper_questions, co_outcomes=co_outcomes_for_template, rbt_totals=rbt_totals)

    except Exception as e:
        print(f"An error occurred: {e}")
        abort(500, description="An internal error occurred while generating the paper.")

if __name__ == '__main__':
    # The port is now set to 8080
    app.run(debug=True, port=8080)