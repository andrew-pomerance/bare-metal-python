'''
 japyc - Just Another PYthon Compiler
(C) 2022 Andrew Pomerance
''' 

import ast
import pprint
import argparse
import errors

from llvmlite import ir, binding
binding.initialize()
binding.initialize_native_target()
binding.initialize_native_asmprinter()

import nodes


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
   
   

class JapycVisitor(ast.NodeVisitor):
    def visit_with_remove(self, nodes):
        '''
        This function visits the children of the input node.  
        It is "with remove" because it removes the parent
        node from the tree; it will be rewrapped in a Japyc*
        AST Node.
        '''
        assert isinstance(nodes, list)
        res = []
        for n in nodes:
            tmp = self.visit(n)
            if tmp is not None:
                res.append(tmp)
        return res


    def __init__(self):
        self.constants = {}

        japyc_classes = [getattr(nodes, c) for c in dir(nodes) if hasattr(getattr(nodes, c), 'derived_from')]

        self.nondefaults = {}
        self.defaults = {}

        for c in japyc_classes:
            if hasattr(c.derived_from, '__iter__'):
                node_names = [d.__name__ for d in c.derived_from]
                default = False
            else:
                node_names = [c.derived_from.__name__]
                default = getattr(c, 'default', False)

            if default:
                if node_names[0] in self.defaults:
                    raise errors.JapycError(f'Multiple default nodes specified for {node_names[0]}')
                self.defaults[node_names[0]] = c
            else:
                for node_name in node_names:
                    if node_name in self.nondefaults:
                        self.nondefaults[node_name].append(c)
                    else:
                        self.nondefaults[node_name] = [c]

    # this is a stub to strip out expression nodes
    def visit_Expr(self, node):
        return self.visit(node.value)
    
    def generic_visit(self, node):
        ast_node_name = node.__class__.__name__
        default_japyc_node = self.defaults.get(ast_node_name, None)
        possible_japyc_nodes = self.nondefaults.get(ast_node_name, [])
        for possible_japyc_node in possible_japyc_nodes:
            res = possible_japyc_node.create_from_node(node, self, self.constants)
            if res is not None:
                return res

        if default_japyc_node:
            return default_japyc_node.create_from_node(node, self, self.constants)

        raise NotImplementedError(f'Unimplemented node: {node}')
        
class LLVMEmitter(ast.NodeVisitor):
    def __init__(self, filename):
        super().__init__()
        self.builder = None
        self.filename = filename
        self.functions = {}
        
    def recurse(self, node_list):
        if node_list:
            return [self.visit(child) for child in node_list]     
        else:
            return []   
      
    def generic_visit(self, node):
        return node.emit_code(self)
        
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

    parser.add_argument('--show-python-ast',
        action='store_true',
        help='Print the AST resulting from lexing Python code'
    )

    parser.add_argument('--show-japyc-ast',
        action='store_true',
        help='Print the transformed japyc AST'
    )

    parser.add_argument('--show-llvm-code',
        action='store_true',
        help='Print LLVM intermediate representation'
    )

    parser.add_argument('--show-all',
        action='store_true',
        help='Equivalent to --show-python-ast --show-japyc-ast --show-llvm-code'
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
                        
    if args.show_all or args.show_python_ast:
        print(ast.dump(ast_root, indent=4))
    japyc_root = JapycVisitor().visit(ast_root)
    if args.show_all or args.show_japyc_ast:
        print(ast.dump(japyc_root, indent=4))
    ir_module = LLVMEmitter(args.input).visit(japyc_root)
    if args.show_all or args.show_llvm_code:
        print(ir_module)
    
    obj_code = compile_ir(ir_module)
    with open(args.output, 'wb') as f:
        f.write(obj_code)
    

if __name__ == '__main__':
    main()
    

        
