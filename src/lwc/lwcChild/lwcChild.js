
import { LightningElement, api, track } from 'lwc';

export default class LwcChild extends LightningElement {
    @api name;
    @track methodCalled = '';
    @track param;

    @api
    childMethod(param) {
        this.param = param;
        this.methodCalled = 'Called From Parent';
    }

}