import ast
import errors

from llvmlite import ir

integer_types = {
    'int8': ir.IntType(8),
    'int16': ir.IntType(16),
    'int32': ir.IntType(32),
    'int64': ir.IntType(64),
}

def JapycMeta(type):
    def __init__(cls, name, bases, dct):
        fields = dct['_fields']
        def __init__(self, *args):
            for field,value in zip(fields, args):
                setattr(self, field, value)
        dct['__init__'] = __init__
        if 'default' not in dct:
            dct['default'] = False
        return super(JapycMeta, cls).__init__(name, bases, dct)

class JapycAST(ast.AST):
    __metaclass__ = JapycMeta

    def emit_code(self, visitor, **kwargs):
        return None

    def get_type(self):
        return None

class JapycModule(JapycAST):
    _fields = ['body']

    # where it comes from
    derived_from = ast.Module
    default = True

    # how to make it
    @staticmethod
    def create_from_node(node, visitor, constants):
        return JapycModule(visitor.visit_with_remove(node.body))
    
    # what to do with it
    def emit_code(self, visitor, **kwargs):
        visitor.module = ir.Module(name=visitor.filename)
        [node.emit_code(visitor, **kwargs) for node in self.body]
        return visitor.module
        

class JapycFunctionDef(JapycAST):
    _fields = ['name', 'args', 'body', 'return_type']

    derived_from = ast.FunctionDef
    default = True

    @staticmethod
    def create_from_node(node, visitor, constants):
        body = visitor.visit_with_remove(node.body)
        # check that all positional args have a valid annotation
        for a in node.args.args:
            if a.annotation is None:
                raise errors.JapycError(f'Error in function definition "{node.name}": argument "{a.arg}" is missing type annotation')
            if a.annotation.id not in integer_types:
                raise errors.JapycError(f'Error in function definition "{node.name}": argument "{a.arg}" has invalid simple type "{a.annotation.id}"')
        
        args = [JapycVariable(a.arg, integer_types[a.annotation.id]) for a in node.args.args]
        if node.returns is None:
            raise errors.JapycError(f'Error in function definition "{node.name}": missing return type definition')
        if isinstance(node.returns, ast.Name):
            if node.returns.id not in integer_types:
                raise errors.JapycError(f'Error in function definition "{node.name}": invalid return type "{node.returns.id}"')
            return_type = integer_types[node.returns.value]
        elif isinstance(node.returns, ast.Constant):
            if node.returns.value == None:
                return_type = ir.VoidType()
            else:
                raise errors.JapycError(f'Error in function definition "{node.name}": invalid return type')
        return JapycFunctionDef(node.name, args, body, return_type)
        
    def emit_code(self, visitor, **kwargs):
        # hard coded return value, hardcoded 64 bit integers
        function_type = ir.FunctionType(self.return_type, [a.type for a in self.args])  
        fn = ir.Function(visitor.module, function_type, name=self.name)
        block = fn.append_basic_block(name='entry')
        visitor.functions[self.name] = fn
        visitor.builder = ir.IRBuilder(block)  # this should be an argument?  it's like a stack I think
        # lookup table for function arguments
        visitor.function_arguments = {ast_arg.name: llvm_arg for ast_arg,llvm_arg in zip(self.args, fn.args)}
        [node.emit_code(visitor, **kwargs) for node in self.body]
        visitor.builder.ret_void()    

class JapycVariable(JapycAST):
    _fields = ['name', 'type']

    derived_from = ast.Name
    default = True

    @staticmethod
    def create_from_node(node, visitor, constants):
        return JapycVariable(node.id)

    def emit_code(self, visitor, **kwargs):
        if self.name in visitor.function_arguments:
            return visitor.function_arguments[self.name]
        else:
            raise NotImplementedError()


class JapycPoke(JapycAST):
    _fields = ['address', 'value', 'type']

    derived_from = ast.Call
    default = False

    @staticmethod
    def create_from_node(node, visitor, constants):
        id = node.func.id
        # is it an attempted poke statement?
        if not id.startswith('_japyc_poke'):
            return None
        
        bits = id[11:]
        if bits not in ('8', '16', '32', '64'):
            raise errors.JapycError(f'Invalid number of bits in poke: {bits}')

        if len(node.args) != 2:
            raise errors.JapycError(f'_japyc_poke* requires exactly 2 arguments')

        return JapycPoke(visitor.visit(node.args[0]),
            visitor.visit(node.args[1]), ir.IntType(int(bits)))

    def emit_code(self, visitor, **kwargs):        
        addr = visitor.builder.inttoptr(self.address.emit_code(visitor, type=ir.IntType(64)), self.type.as_pointer())
        value = self.value.emit_code(visitor, type=self.type)
        visitor.builder.store(value, addr)

            
class JapycFunctionCall(JapycAST):
    _fields = ['fn', 'args']

    derived_from = ast.Call
    default = True

    @staticmethod
    def create_from_node(node, visitor, constants):
        args = visitor.visit_with_remove(node.args)
        return JapycFunctionCall(node.func.id, args)

    def emit_code(self, visitor, **kwargs):
        arg_types = visitor.functions[self.fn].ftype.args
        args = [arg.emit_code(visitor, type=arg_type) for arg, arg_type in zip(self.args, arg_types)]
        visitor.builder.call(visitor.functions[self.fn], args)
       
class JapycInteger(JapycAST):
    _fields = ['value', 'type']

    derived_from = (
        ast.Constant,   # obviously
        ast.Name,       # a named constant
        ast.Attribute,  # an Enum value
        ast.Call        # an explicitly typed integer
    )

    @staticmethod    
    def create_from_node(node, visitor, constants):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int):
                return JapycInteger(node.value, None)
            elif isinstance(node.value, str):
                if len(node.value) == 1 and ord(node.value) < 128:
                    return JapycInteger(ord(node.value), None)
            else:
                return None
        elif isinstance(node, ast.Name):
            if node.id in constants:
                if not isinstance(constants[node.id], int):
                    raise errors.JapycError(f'{node.id} is an enum, not a constant')
                return JapycInteger(constants[node.id], None)
            else:
                return None
        elif isinstance(node, ast.Attribute):
            if not isinstance(node.value, ast.Name):
                return None
            if node.value.id not in constants:
                return None
            if not isinstance(constants[node.value.id], dict):
                raise errors.JapycError(f'{node.value.id} is a constant, not an enum')
            if node.attr not in constants[node.value.id]:
                raise errors.JapycError(f'{node.attr} is not a member of the {node.value.id} enum')
            return JapycInteger(constants[node.value.id][node.attr], None)
        elif isinstance(node, ast.Call):
            return None
        return None

    def emit_code(self, visitor, **kwargs):  
        if self.type is None:
            if 'type' not in kwargs:
                int_type = ir.IntType(64)  # default to 64-bits
            else:
                int_type = kwargs['type']
        else:
            if 'type' in kwargs:
                if self.type !=  kwargs['type']:
                    raise errors.JapycError(f'type mismatch: {self.type} and {kwargs["type"]}')
                int_type = self.int_type

        # TODO: BOUNDS CHECKING
        return ir.Constant(int_type, self.value)
    
class JapycChar(JapycAST):
    _fields = ['value']
        
class JapycBinOp(JapycAST):
    _fields = ['op', 'left', 'right']

    derived_from = ast.BinOp
    default = True
        
    @staticmethod
    def create_from_node(node, visitor, constants):
        left = visitor.visit(node.left)
        right = visitor.visit(node.right)
        def _do_op(x, y):
            if isinstance(node.op, ast.Mult):
                return x*y
            elif isinstance(node.op, ast.Add):
                return x+y
            else:
                raise NotImplementedError()

        if isinstance(left, JapycInteger) and isinstance(right, JapycInteger):
            return JapycInteger(_do_op(left.value, right.value), None)
        else:
            return JapycBinOp(node.op, left, right)

    def emit_code(self, visitor, **kwargs):
        a = self.left.emit_code(visitor, **kwargs)
        b = self.right.emit_code(visitor, **kwargs)
        if isinstance(self.op, ast.Add):
            return visitor.builder.add(a, b)
        elif isinstance(self.op, ast.Mult):
            return visitor.builder.mul(a, b)

class JapycEnum(JapycAST):
    _fields = []

    derived_from = ast.ClassDef

    @staticmethod
    def create_from_node(node, visitor, constants):
        if len(node.decorator_list) != 1:
            return None
        if node.decorator_list[0].id != '_japyc_Enum':
            return None

        enum_fields = {}
        for enum_node in node.body:
            # each node in an enum classdef body is an Assign node 
            if not isinstance(enum_node, ast.Assign):
                raise errors.JapycError('Error in _japyc_Enum: not an assignment')
            if len(enum_node.targets) != 1:
                raise errors.JapycError('Error in _japyc_Enum: multiple targets to assignment')
            if not isinstance(enum_node.targets[0], ast.Name):
                raise errors.JapycError('Error in _japyc_Enum: not assigning to a name')
            if not isinstance(enum_node.value, ast.Num):
                raise errors.JapycError('Error in _japyc_Enum: not assigning a number')
            enum_fields[enum_node.targets[0].id] = enum_node.value.n
        constants[node.name] = enum_fields
        return JapycEnum()

class JapycConst(JapycAST):
    _fields = []

    derived_from = ast.Call

    @staticmethod
    def create_from_node(node, visitor, constants):
        id = node.func.id
        # is it an attempted constant statement?
        if not id.startswith('_japyc_const'):
            return None
        if len(node.keywords) != 1:
            raise errors.JapycError('Error in _japyc_constant: exactly one keyword per call')
        if not isinstance(node.keywords[0].value, ast.Num):
            raise errors.JapycError('Error in _japyc_constant: assignment must be an integer')
        constant = node.keywords[0].arg
        value = node.keywords[0].value.n
        if constant in constants:
            raise errors.JapycError('Error in _japyc_constant: {constant} previously defined')
        constants[constant] = value
        

        return JapycConst()
    
