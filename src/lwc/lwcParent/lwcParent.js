
import { LightningElement } from 'lwc';

export default class LwcParent extends LightningElement {

    handleButtonClick() {
        this.template.querySelector('c-lwc-child').childMethod({firstName: 'Homer', lastName: 'Simpson'});
    }

}