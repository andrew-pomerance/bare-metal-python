import ast
import errors

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
    

class JapycFunctionDef(JapycAST):
    _fields = ['name', 'args', 'body']

    derived_from = ast.FunctionDef
    default = True

    @staticmethod
    def create_from_node(node, visitor, constants):
        body = visitor.visit_with_remove(node.body)
        args = [JapycVariable(a.arg) for a in node.args.args]
        return JapycFunctionDef(node.name, args, body)
        
class JapycVariable(JapycAST):
    _fields = ['name']

    derived_from = ast.Name
    default = True

    @staticmethod
    def create_from_node(node, visitor, constants):
        return JapycVariable(node.id)

class JapycPoke(JapycAST):
    _fields = ['address', 'value', 'bits']

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
            visitor.visit(node.args[1]), int(bits))
            
class JapycFunctionCall(JapycAST):
    _fields = ['fn', 'args']

    derived_from = ast.Call
    default = True

    @staticmethod
    def create_from_node(node, visitor, constants):
        args = visitor.visit_with_remove(node.args)
        return JapycFunctionCall(node.func.id, args)
       
class JapycInteger(JapycAST):
    _fields = ['value']

    derived_from = (
        ast.Constant,  # obviously
        ast.Name,      # a named constant
        ast.Attribute  # an Enum value
    )

    @staticmethod    
    def create_from_node(node, visitor, constants):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int):
                return JapycInteger(node.value)
            elif isinstance(node.value, str):
                if len(node.value) == 1 and ord(node.value) < 128:
                    return JapycInteger(ord(node.value))
            else:
                return None
        elif isinstance(node, ast.Name):
            if node.id in constants:
                if not isinstance(constants[node.id], int):
                    raise errors.JapycError(f'{node.id} is an enum, not a constant')
                return JapycInteger(constants[node.id])
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
            return JapycInteger(constants[node.value.id][node.attr])
        
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
            return JapycInteger(_do_op(left.value, right.value))
        else:
            return JapycBinOp(node.op, left, right)

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
    
