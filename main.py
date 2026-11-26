import argparse
from pathlib import Path
from orchestrator import run_validation, print_summary
from core.advanced_report import generate_pdf, generate_quick_pdf

def main():
    p=argparse.ArgumentParser(description='AI Requirements Validator')
    p.add_argument('-i','--input',required=True,type=Path)
    p.add_argument('-o','--output',required=True,type=Path)
    p.add_argument('--mode',choices=['quick','deep'],default='deep')
    p.add_argument('--cache',action='store_true')
    p.add_argument('--force',action='store_true')
    args=p.parse_args()
    if not args.input.is_file() or args.input.suffix.lower() not in ('.pdf','.docx','.md','.txt'):
        p.error('Input must be an existing PDF, DOCX, MD or TXT file')
    if args.output.suffix.lower()!='.pdf' or args.output.resolve()==args.input.resolve():
        p.error('Output must be a separate PDF file')
    try:
        result=run_validation(args.input, reports_dir=args.output.parent, use_cache=args.cache, force_reanalysis=args.force, mode=args.mode)
        print_summary(result)
        (generate_quick_pdf if args.mode=='quick' else generate_pdf)(result,args.output)
        print('[PDF]',args.output)
        return 0
    except Exception as exc:
        print('[ERROR]',exc)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
