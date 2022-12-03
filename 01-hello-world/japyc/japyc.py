'''
 japyc - Just Another PYthon Compiler
(C) 2022 Andrew Pomerance
''' 

import ast
import pprint
import argparse

from llvmlite import ir, binding
binding.initialize()
binding.initialize_native_target()
binding.initialize_native_asmprinter()


def ast2tree(node, include_attrs=True):
    def _transform(node):
        if isinstance(node, ast.AST):
            fields = ((a, _transform(b))
                      for a, b in ast.iter_fields(node))
            if include_attrs:
                attrs = ((a, _transform(getattr(node, a)))
                         for a in node._attributes
                         if hasattr(node, a))
                return (node.__class__.__name__, dict(fields), dict(attrs))
            return (node.__class__.__name__, dict(fields))
        elif isinstance(node, list):
            return [_transform(x) for x in node]
        elif isinstance(node, str):
            return repr(node)
        return node
    if not isinstance(node, ast.AST):
        raise TypeError('expected AST, got %r' % node.__class__.__name__)
    return _transform(node)

def pformat_ast(node, include_attrs=False, **kws):
    return pprint.pformat(ast2tree(node, include_attrs), **kws)
   
class JapycModule(ast.AST):
    _fields = ['body']
    def __init__(self, body):
        self.body = body
        
class JapycFunction(ast.AST):
    _fields = ['name', 'args', 'body']
    def __init__(self, name, args, body):
        self.name = name
        self.args = args
        self.body = body
        
class JapycVariable(ast.AST):
    _fields = ['name']
    def __init__(self, name):  
        self.name = name

class JapycPutInt(ast.AST):
    _fields = ['address', 'value', 'bits']
    def __init__(self, address, value, bits):
        self.address = address
        self.value = value
        self.bits = bits
    
class JapycVisitor(ast.NodeVisitor):
    def visit_Module(self, node):
        return JapycModule([self.visit(n) for n in node.body])
    
    def visit_Name(self, node):
        return JapycVariable(node.id)
    
    def visit_FunctionDef(self, node):
        args = [self.visit(n) for n in node.args.args]
        body = [self.visit(n) for n in node.body]
        return JapycFunction(node.name, args, body)

    def visit_Expr(self, node):
        return self.visit(node.value)
    
    def visit_Call(self, node):  
        if node.func.id == 'poke64':
            memory_address = node.args[0].n
            value = node.args[1].n
            return JapycPutInt(memory_address, value, 64)
        else:
            raise NotImplementedError()
        
    def generic_visit(self, node):
        raise NotImplementedError()
        


class LLVMEmitter(ast.NodeVisitor):
    def __init__(self, filename):
        super().__init__()
        self.builder = None
        self.filename = filename
        
    def _recurse(self, node_list):
        for child in node_list:
            self.visit(child)
        
    def visit_JapycModule(self, node):
        self.module = ir.Module(name=self.filename)
        self._recurse(node.body)
        return self.module
        
    def visit_JapycFunction(self, node):
        function_type = ir.FunctionType(ir.VoidType(), [])  # hard coded for now
        func = ir.Function(self.module, function_type, name=node.name)
        block = func.append_basic_block(name='entry')
        self.builder = ir.IRBuilder(block)
        
        self._recurse(node.body)
            
        self.builder.ret_void()
        
    def visit_JapycPutInt(self, node):
        int_type = ir.IntType(node.bits)        
        addr = self.builder.inttoptr(ir.Constant(int_type, node.address), int_type.as_pointer())
        value = ir.Constant(int_type, node.value)
        self.builder.store(value, addr)
        
def compile_ir(ir_module):
    """
    Compile the LLVM IR string with the given engine.
    The compiled module object is returned.
    """
    # Create a target machine representing the host
    target = binding.Target.from_default_triple()
    target_machine = target.create_target_machine()
    # And an execution engine with an empty backing module
    binding_module = binding.parse_assembly(str(ir_module))
    return target_machine.emit_object(binding_module)

def get_args():
    parser = argparse.ArgumentParser(
        prog='japyc',
        description='Just Another PYthon Compiler'
        )

    def _valid_filename(ext):
        def inner(filename):
            if not filename.endswith(ext):
                raise argparse.ArgumentTypeError(f'file extension must be {ext}')
            return filename
        return inner

    parser.add_argument('input', 
        help='Input Python file to compile (must be .py)', 
        type=_valid_filename('.py'))

    parser.add_argument('-o', 
        dest='output', 
        type=_valid_filename('.o'),
        help='Output object file (must be .o, defaults to <input>.o)',
        default=argparse.SUPPRESS)

    parser.add_argument('--verbose',
        action='store_true',
        help='Print additional data'
    )

    args = parser.parse_args()

    if not hasattr(args, 'output'):
        setattr(args, 'output', args.input[:-3]+'.o')


    return args


def main():

    args = get_args()
        
    with open(args.input, 'r') as f:
        python_source = f.read()
    ast_root = ast.parse(python_source, filename=args.input)
                        
    if args.verbose:
        print(pformat_ast(ast_root))
    japyc_root = JapycVisitor().visit(ast_root)
    if args.verbose:
        print(pformat_ast(japyc_root))
    ir_module = LLVMEmitter(args.input).visit(japyc_root)
    if args.verbose:
        print(ir_module)
    
    obj_code = compile_ir(ir_module)
    with open(args.output, 'wb') as f:
        f.write(obj_code)
    

if __name__ == '__main__':
    main()
    

        
